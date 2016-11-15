# Radioco - Broadcasting Radio Recording Scheduling system.
# Copyright (C) 2014  Iago Veloso Abalo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import heapq
from functools import partial
from itertools import imap

import pytz

from radioco.apps.schedules.utils import rearrange_episodes
from radioco.apps.radioco.utils import field_has_changed
from radioco.apps.radioco.tz_utils import transform_datetime_tz, convert_date_to_datetime, \
    transform_dt_checking_dst, fix_recurrence_dst, fix_dst_tz, GMT, transform_dt_to_default_tz
from radioco.apps.programmes.models import Programme, Episode
from dateutil import rrule
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.template.defaultfilters import slugify
from django.utils import six
from django.utils.translation import ugettext_lazy as _
from django.db.models.signals import pre_save
from recurrence.fields import RecurrenceField
import datetime
from django.utils import timezone


EMISSION_TYPE = (
    ("L", _("live")),
    ("B", _("broadcast")),
    ("S", _("broadcast syndication"))
)

MO = 0
TU = 1
WE = 2
TH = 3
FR = 4
SA = 5
SU = 6

WEEKDAY_CHOICES = (
    (MO, _('Monday')),
    (TU, _('Tuesday')),
    (WE, _('Wednesday')),
    (TH, _('Thursday')),
    (FR, _('Friday')),
    (SA, _('Saturday')),
    (SU, _('Sunday')),
)


class CalendarManager(models.Manager):

    def current(self):
        return Calendar.objects.get(is_active=True)


class Calendar(models.Model):
    class Meta:
        verbose_name = _('calendar')
        verbose_name_plural = _('calendar')

    name = models.CharField(max_length=255, unique=True, verbose_name=_("name"))
    is_active = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.is_active:
            active_calendars = Calendar.objects.filter(is_active=True)
            active_calendars.update(is_active=False)
        super(Calendar, self).save(*args, **kwargs)

    def __unicode__(self):
        return u"%s" % (self.name)

# TODO: what should happen when a Calendar is deleted or other calendar is set as active
# TODO:
# @receiver(post_delete, sender=Calendar)
# def delete_Calendar_handler(sender, **kwargs):
#     now = timezone.now()
#     for programme in Programme.objects.all():
#         rearrange_episodes(programme, now)


class ExcludedDates(models.Model):
    """
    Helper to improve performance
    """
    schedule = models.ForeignKey('Schedule')
    datetime = models.DateTimeField(db_index=True)

    @property
    def date(self):
        local_dt = transform_dt_to_default_tz(self.datetime)
        return local_dt.date()

    def get_new_excluded_datetime(self, new_dt):
        """
        Returns: A new dt to be excluded in that date
        """
        default_tz = timezone.get_default_timezone()
        new_dt_in_default_tz = transform_datetime_tz(new_dt, tz=default_tz)
        default_tz.localize(datetime.datetime.combine(self.date, new_dt_in_default_tz.time()))


class Schedule(models.Model):
    class Meta:
        verbose_name = _('schedule')
        verbose_name_plural = _('schedules')

    programme = models.ForeignKey(Programme, verbose_name=_("programme"))
    type = models.CharField(verbose_name=_("type"), choices=EMISSION_TYPE, max_length=1)
    calendar = models.ForeignKey(Calendar, verbose_name=_("calendar"))
    recurrences = RecurrenceField(verbose_name=_("recurrences"))

    start_dt = models.DateTimeField(verbose_name=_('start date'))

    end_dt = models.DateTimeField(  # TODO: check if it's necessary
        blank=True, null=True, verbose_name=_('end date'),
        help_text=_('This field is dynamically generated based on the programme duration')
    )

    effective_start_dt = models.DateTimeField(
        blank=True, null=True, verbose_name=_('first effective start date'),
        help_text=_('This field is dynamically generated to improve performance')
    )

    effective_end_dt = models.DateTimeField(
        blank=True, null=True, verbose_name=_('last effective end date'),
        help_text=_('This field is dynamically generated to improve performance')
    )

    from_collection = models.ForeignKey(
        'self', blank=True, null=True, on_delete=models.SET_NULL, related_name='child_schedules',
        help_text=_("Parent schedule (only happens when it is changed from recurrence.")
    )

    source = models.ForeignKey(
        'self', blank=True, null=True, on_delete=models.SET_NULL, verbose_name=_("source"),
        help_text=_("Main schedule when (if this is a broadcast).")
    )

    def save(self, *args, **kwargs):
        assert self.start_dt, 'start_dt is required'
        self._clean_recurrence_dates()
        if field_has_changed(self, 'start_dt'):
            self._update_excluded_dates()

        self.effective_end_dt = calculate_effective_schedule_end_dt(self)
        self.effective_start_dt = calculate_effective_schedule_start_dt(self)

        self.end_dt = self.start_dt + self.runtime

        super(Schedule, self).save(*args, **kwargs)

        rearrange_episodes(self.programme, timezone.now())

    def _clean_recurrence_dates(self):
        """
        We want to include the whole day
        """
        default_tz = timezone.get_default_timezone()
        for rrule in self.recurrences.rrules:
            if rrule.until:
                rrule.until = default_tz.localize(datetime.datetime.combine(
                    transform_dt_to_default_tz(rrule.until).date(), datetime.time(23, 59, 59)))

    def _update_excluded_dates(self):
        """
        We need to update dates inside ExcludedDates and the recurrence library
        """
        exdates = []
        for excluded in ExcludedDates.objects.filter(schedule=self):
            new_excluded_dt = excluded.get_new_excluded_datetime(self.start_dt)
            excluded.datetime = new_excluded_dt
            excluded.save()
            exdates.append(self._fix_recurrence_date(new_excluded_dt))
        self.recurrences.exdates = exdates

    @property
    def runtime(self):
        return self.programme.runtime

    @staticmethod
    def get_schedule_which_excluded_dt(programme, dt):
        try:
            return ExcludedDates.objects.get(schedule__programme=programme, datetime=dt).schedule
        except ExcludedDates.DoesNotExist:
            return None

    def exclude_date(self, dt):
        local_dt = transform_datetime_tz(dt)
        ExcludedDates.objects.create(schedule=self, datetime=dt)

        exdate = self._fix_recurrence_date(local_dt)
        self.recurrences.exdates.append(exdate)

    def include_date(self, dt):
        local_dt = transform_datetime_tz(dt)
        ExcludedDates.objects.get(schedule=self, datetime=dt).delete()

        exdate = self._fix_recurrence_date(local_dt)
        self.recurrences.exdates.remove(exdate)

    def has_recurrences(self):
        return self.recurrences

    def dates_between(self, after, before):
        """
            Return a sorted list of dates between after and before
        """
        after_date = self._merge_after(after)
        if not after_date:
            return
        after_date = transform_dt_to_default_tz(after_date)
        before_date = transform_dt_to_default_tz(self._merge_before(before))
        start_dt = transform_dt_to_default_tz(self.start_dt)

        # We need to send the dates in the current timezone
        recurrence_dates_between = self.recurrences.between(after_date, before_date, inc=True, dtstart=start_dt)

        for date in recurrence_dates_between:
            dt = fix_recurrence_dst(date, requested_tz=after.tzinfo)  # Truncate date
            yield dt

    def date_before(self, before):
        before_date = transform_dt_to_default_tz(self._merge_before(before))
        start_dt = transform_dt_to_default_tz(self.start_dt)
        date = self.recurrences.before(before_date, inc=True, dtstart=start_dt)
        return fix_recurrence_dst(date, requested_tz=before.tzinfo)

    def date_after(self, after):
        after_date = self._merge_after(after)
        if not after_date:
            return
        after_date = transform_dt_to_default_tz(after_date)
        start_dt = transform_dt_to_default_tz(self.start_dt)
        date = self.recurrences.after(after_date, inc=True, dtstart=start_dt)
        return fix_recurrence_dst(date, requested_tz=after.tzinfo)

    def _fix_recurrence_date(self, dt):
        """
        Fix for django-recurrence 1.3
        rdates and exdates needs a datetime, we are combining the date with the time from start_date.

        Return: A datetime in the *local timezone*
        """
        current_dt = transform_dt_to_default_tz(dt)
        current_start_dt = transform_dt_to_default_tz(self.start_dt)

        tz = GMT(current_start_dt.utcoffset().total_seconds())  # start_date DST naive timezone
        fixed_dt = transform_dt_to_default_tz(
            tz.localize(datetime.datetime.combine(current_dt.date(), current_start_dt.time()))
        )
        return fixed_dt

    def _merge_after(self, after):
        """
        Return the greater first date taking into account the programme constraints
        """
        if not self.effective_start_dt:
            return None
        return max(after, self.effective_start_dt)

    def _merge_before(self, before):
        """
        Return the smaller last date taking into account the programme constraints
        """
        if not self.effective_end_dt:
            return before
        return min(before, self.effective_end_dt)

    def __unicode__(self):
        return ' - '.join([self.start_dt.strftime('%A'), self.start_dt.strftime('%X')])


def calculate_effective_schedule_start_dt(schedule):
    """
    Calculation of end_dt to improve performance
    """
    # If there are no rrules
    programme_start_dt = schedule.programme.start_dt
    if not schedule.has_recurrences():
        if not programme_start_dt or programme_start_dt <= schedule.start_dt:
            return schedule.start_dt
        return None

    # Get first date
    after_dt = schedule.start_dt
    if schedule.programme.start_dt:
        after_dt = max(schedule.start_dt, schedule.programme.start_dt)
    first_start_dt = schedule.recurrences.after(
        transform_dt_to_default_tz(after_dt), True, dtstart=transform_dt_to_default_tz(schedule.start_dt))
    if first_start_dt:
        if schedule.programme.end_dt and schedule.programme.end_dt < first_start_dt:
            return None
        return fix_recurrence_dst(first_start_dt)
    return None


def calculate_effective_schedule_end_dt(schedule):
    """
    Calculation of end_dt to improve performance
    """
    programme_end_dt = schedule.programme.end_dt

    # If there are no rrules
    if not schedule.has_recurrences():
        if not programme_end_dt or programme_end_dt >= schedule.start_dt:
            return schedule.start_dt + schedule.runtime
        return None

    # If we have a programme restriction
    if programme_end_dt:
        last_effective_start_date = schedule.recurrences.before(
            transform_dt_to_default_tz(programme_end_dt), dtstart=transform_dt_to_default_tz(schedule.start_dt))
        if last_effective_start_date:
            if schedule.programme.start_dt and schedule.programme.start_dt > last_effective_start_date:
                return None
            return fix_recurrence_dst(last_effective_start_date) + schedule.runtime

    rrules_until_dates = [_rrule.until for _rrule in schedule.recurrences.rrules]

    # If we have a rrule without a until date we cannot know the last date
    if any(map(lambda x: x is None, rrules_until_dates)):
        return None

    # Get the biggest possible start_date. It could be that the biggest date is excluded
    possible_limit_dates = schedule.recurrences.rdates + rrules_until_dates
    biggest_date = max(possible_limit_dates)
    last_effective_start_date = schedule.recurrences.before(
        transform_dt_to_default_tz(biggest_date), True, dtstart=transform_dt_to_default_tz(schedule.start_dt))
    if last_effective_start_date:
        if schedule.programme.start_dt and schedule.programme.start_dt > last_effective_start_date:
            return None
        return fix_recurrence_dst(last_effective_start_date) + schedule.runtime
    return None


# def update_performance_in_schedule(sender, instance, **kwargs):
#     instance.effective_end_dt = calculate_effective_schedule_end_dt(instance)
#     instance.effective_start_dt = calculate_effective_schedule_start_dt(instance)
# 
# pre_save.connect(update_performance_in_schedule, sender=Schedule, dispatch_uid='calculate_effective_schedule_end_dt')


# XXX entry point for transmission details (episode, recordings, ...)
class Transmission(object):
    def __init__(self, schedule, date):
        self.schedule = schedule
        self.start = date

    @property
    def programme(self):
        return self.schedule.programme

    @property
    def name(self):
        return self.programme.name

    @property
    def end(self):
        return self.start + self.schedule.runtime

    @property
    def slug(self):
        return self.programme.slug

    @property
    def url(self):
        return reverse('programmes:detail', args=[self.programme.slug])

    @classmethod
    def at(cls, at):
        schedules = Schedule.objects.filter(
            Q(effective_start_dt__lte=at, effective_end_dt__gt=at) |
            Q(effective_start_dt__lte=at, effective_end_dt__isnull=True)
        ) # TODO: check!!
        for schedule in schedules:
            date = schedule.date_before(at)
            if date and date <= at < date + schedule.runtime:
                yield cls(schedule, date)

    @classmethod
    def between(cls, after, before, schedules=None):
        """
        Return a list of Transmissions of the active calendar sorted by date
        """
        if schedules is None:
            schedules = Schedule.objects.filter(calendar__is_active=True)

        transmission_dates = [
            imap(partial(_return_tuple, item2=schedule), schedule.dates_between(after, before))
            for schedule in schedules
        ]
        sorted_transmission_dates = heapq.merge(*transmission_dates)
        for sorted_transmission_date, schedule in sorted_transmission_dates:
            yield cls(schedule, sorted_transmission_date)


def _return_tuple(item1, item2):
    return item1, item2

#def __get_events(after, before, json_mode=False):
#    background_colours = {"L": "#F9AD81", "B": "#C4DF9B", "S": "#8493CA"}
#    text_colours = {"L": "black", "B": "black", "S": "black"}
#
#    next_schedules, next_dates = Schedule.between(after=after, before=before)
#    schedules = []
#    dates = []
#    episodes = []
#    event_list = []
#    for x in range(len(next_schedules)):
#        for y in range(len(next_dates[x])):
#            # next_events.append([next_schedules[x], next_dates[x][y]])
#            schedule = next_schedules[x]
#            schedules.append(schedule)
#            date = next_dates[x][y]
#            dates.append(date)
#
#            episode = None
#            # if schedule == live
#            if next_schedules[x].type == 'L':
#                try:
#                    episode = Episode.objects.get(issue_date=date)
#                except Episode.DoesNotExist:
#                    pass
#            # broadcast
#            elif next_schedules[x].source:
#                try:
#                    source_date = next_schedules[x].source.date_before(date)
#                    if source_date:
#                        episode = Episode.objects.get(issue_date=source_date)
#                except Episode.DoesNotExist:
#                    pass
#            episodes.append(episode)
#
#            if episode:
#                url = reverse(
#                    'programmes:episode_detail',
#                    args=(schedule.programme.slug, episode.season, episode.number_in_season,)
#                )
#            else:
#                url = reverse('programmes:detail', args=(schedule.programme.slug,))
#
#            event_entry = {
#                'id': schedule.id,
#                'start': str(date),
#                'end': str(date + schedule.runtime),
#                'allDay': False,
#                'title':  schedule.programme.name,
#                'type': schedule.type,
#                'textColor': text_colours[schedule.type],
#                'backgroundColor': background_colours[schedule.type],
#                'url': url
#            }
#            event_list.append(event_entry)
#
#    if json_mode:
#        return event_list
#    else:
#        if schedules:
#            dates, schedules, episodes = (list(t) for t in zip(*sorted(zip(dates, schedules, episodes))))
#            return zip(schedules, dates, episodes)
#        return None
