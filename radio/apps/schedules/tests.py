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

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, FieldError
from django.core.urlresolvers import reverse
from django.test import TestCase
import datetime
import recurrence

from apps.programmes.models import Programme, Episode
from apps.radio.tests import TestDataMixin
from apps.schedules import utils
from apps.schedules.models import MO, TU, WE, TH, FR, SA, SU
from apps.schedules.models import ScheduleBoard, Schedule, ScheduleBoardManager


class ScheduleModelTests(TestCase):
    def setUp(self):
        self.schedule_board = ScheduleBoard.objects.create(
            name='Board',
            start_date=datetime.date(2014, 1, 1),
            end_date=datetime.date(2014, 6, 1))

        self.recurrences = recurrence.Recurrence(
            dtstart=datetime.datetime(2014, 1, 6, 14, 0, 0),
            dtend=datetime.datetime(2014, 1, 31, 14, 0, 0),
            rrules=[recurrence.Rule(recurrence.WEEKLY)])

        self.schedule = Schedule.objects.create(
            programme=Programme.objects.create(
                name="Programme 14:00 - 15:00",
                synopsis="This is a description",
                current_season=1,
                runtime=60),
            type='L',
            recurrences=self.recurrences,
            schedule_board=self.schedule_board)

    def test_runtime(self):
        self.assertEqual(datetime.timedelta(hours=+1), self.schedule.runtime)

    def test_runtime_not_set(self):
        schedule = Schedule(programme=Programme())
        with self.assertRaises(FieldError):
            schedule.runtime

    def test_start_date_schedule_board_none(self):
        schedule = Schedule(
            recurrences=self.recurrences,
            programme=Programme(),
            schedule_board=ScheduleBoard())
        self.assertEqual(schedule.start, datetime.datetime(2014, 1, 6, 14, 0))

    def test_start(self):
        self.assertEqual(
            self.schedule.start, datetime.datetime(2014, 1, 6, 14, 0, 0))

    def test_set_start(self):
        self.schedule.start = datetime.datetime(2015, 1, 1, 14, 0, 0)
        self.assertEqual(
            self.schedule.recurrences.dtstart,
            datetime.datetime(2015, 1, 1, 14, 0, 0))

    def test_start_lt_schedule_board(self):
        self.schedule_board.start_date = datetime.date(2014, 1, 14)
        self.assertEqual(
            self.schedule.start, datetime.datetime(2014, 1, 14, 0, 0, 0))

    def test_start_none(self):
        schedule = Schedule(
            programme=Programme(), schedule_board=ScheduleBoard())
        self.assertIsNone(schedule.start)

    def test_end(self):
        self.assertEqual(
            self.schedule.end, datetime.datetime(2014, 1, 31, 14, 0, 0))

    def test_end_gt_schedule_board(self):
        self.schedule_board.end_date = datetime.date(2014, 1, 14)
        self.assertEqual(
            self.schedule.end, datetime.datetime(2014, 1, 14, 23, 59, 59))

    def test_end_none(self):
        schedule = Schedule(
            programme=Programme(), schedule_board=ScheduleBoard())
        self.assertIsNone(schedule.end)

    def test_recurrence_rules(self):
        self.assertListEqual(
            self.schedule.recurrences.rrules, self.recurrences.rrules)

    def test_date_before(self):
        self.assertEqual(
            self.schedule.date_before(datetime.datetime(2014, 1, 14)),
            datetime.datetime(2014, 1, 13, 14, 0))

    def test_date_before_erlier_end_by_board(self):
        self.schedule_board.end_date = datetime.date(2014, 1, 18)
        self.assertEqual(
            self.schedule.date_before(datetime.datetime(2014, 1, 30)),
            datetime.datetime(2014, 1, 13, 14, 0))

    def test_date_after(self):
        self.assertEqual(
            self.schedule.date_after(datetime.datetime(2014, 1, 14)),
            datetime.datetime(2014, 1, 20, 14, 0))

    def test_date_after_exclude(self):
        self.assertEqual(
            self.schedule.date_after(
                datetime.datetime(2014, 1, 6, 14, 0), inc=False),
            datetime.datetime(2014, 1, 13, 14, 0))

# XXX see Schedule._merge_after
#    def test_date_after_running(self):
#        self.assertEqual(
#            self.schedule.date_after(datetime.datetime(2014, 1, 6, 14, 30)),
#            datetime.datetime(2014, 1, 6, 14, 0))

    def test_date_after_later_start_by_board(self):
        self.schedule_board.start_date = datetime.date(2014, 1, 7)
        self.assertEqual(
            self.schedule.date_after(datetime.datetime(2014, 1, 1)),
            datetime.datetime(2014, 1, 13, 14, 0))

    def test_dates_between(self):
        self.assertEqual(
            self.schedule.dates_between(
                datetime.datetime(2014, 1, 1), datetime.datetime(2014, 1, 14)),
            [datetime.datetime(2014, 1, 6, 14, 0),
             datetime.datetime(2014, 1, 13, 14, 0)])

    def test_dates_between_complex_ruleset(self):
        schedule = Schedule(
            programme=Programme(name="Programme 14:00 - 15:00", runtime=60),
            schedule_board=self.schedule_board,
            recurrences=recurrence.Recurrence(
                dtstart=datetime.datetime(2014, 1, 2, 14, 0, 0),
                rrules=[recurrence.Rule(recurrence.DAILY, interval=2)],
                exrules=[recurrence.Rule(
                    recurrence.WEEKLY, byday=[recurrence.MO, recurrence.TU])]))

        self.assertListEqual(
            schedule.dates_between(
                datetime.datetime(2014, 1, 1), datetime.datetime(2014, 1, 9)),
            [datetime.datetime(2014, 1, 2, 14, 0),
             datetime.datetime(2014, 1, 4, 14, 0),
             datetime.datetime(2014, 1, 8, 14, 0)])

    def test_dates_between_later_start_by_board(self):
        self.schedule_board.start_date = datetime.date(2014, 1, 7)
        self.assertListEqual(
            self.schedule.dates_between(
                datetime.datetime(2014, 1, 1), datetime.datetime(2014, 1, 14)),
            [datetime.datetime(2014, 1, 13, 14, 0)])

    def test_dates_between_erlier_end_by_board(self):
        self.schedule_board.end_date = datetime.date(2014, 1, 7)
        self.assertListEqual(
            self.schedule.dates_between(
                datetime.datetime(2014, 1, 1), datetime.datetime(2014, 1, 14)),
            [datetime.datetime(2014, 1, 6, 14, 0)])

# XXX see Schedule._merge_after
#    def test_dates_between_running(self):
#        self.assertEqual(
#            self.schedule.dates_between(
#                datetime.datetime(2014, 1, 6, 14, 30),
#                datetime.datetime(2014, 1, 7)),
#            [datetime.datetime(2014, 1, 6, 14, 0)])

    def test_unicode(self):
        self.assertEqual(unicode(self.schedule), 'Monday - 14:00:00')


#class ScheduleClassModelTests(TestCase):
#    def setUp(self):
#        daily = recurrence.Recurrence(
#            rrules=[recurrence.Rule(recurrence.DAILY)])
#
#        weekly = recurrence.Recurrence(
#            rrules=[recurrence.Rule(recurrence.WEEKLY)])
#
#        self.schedule_board = ScheduleBoard.objects.create(
#            name='Board', start_date=datetime.datetime(2014, 1, 1, 0, 0, 0, 0))
#
#        midnight_programme = Programme.objects.create(
#            name="Programme 00:00 - 09:00",
#            synopsis="This is a description",
#            current_season=1, runtime=540)
#
#        Schedule.objects.create(
#            programme=midnight_programme,
#            day=WE,
#            start_hour=datetime.time(0, 0, 0),
#            type='L',
#            schedule_board=self.schedule_board)
#
#        programme = Programme.objects.create(
#            name="Programme 09:00 - 10:00",
#            synopsis="This is a description",
#            current_season=1, runtime=60)
#
#        for day in (MO, WE, FR):
#            Schedule.objects.create(
#                programme=programme, day=day, type='L',
#                schedule_board=self.schedule_board)
#
#        programme = Programme.objects.create(
#            name="Programme 10:00 - 12:00",
#            synopsis="This is a description",
#            current_season=1, runtime=120)
#
#        for day in (MO, WE, FR):
#            Schedule.objects.create(
#                programme=programme,
#                day=day, type='B',
#                start_hour=datetime.time(10, 0, 0),
#                schedule_board=self.schedule_board)
#
#        for schedule in Schedule.objects.all():
#            schedule.clean()
#
#    def test_day_schedule(self):
#        schedules, dates = Schedule.between(datetime.datetime(2014, 1, 6), datetime.datetime(2014, 1, 7))
#        self.assertEqual(3, len(schedules))
#
#        schedule_1 = Schedule.objects.get(programme=Programme.objects.get(name="Programme 00:00 - 09:00"), day=WE)
#        schedule_2 = Schedule.objects.get(programme=Programme.objects.get(name="Programme 09:00 - 10:00"), day=MO)
#        schedule_3 = Schedule.objects.get(programme=Programme.objects.get(name="Programme 10:00 - 12:00"), day=MO)
#        self.assertTrue(schedule_1 in schedules)
#        self.assertTrue(schedule_2 in schedules)
#        self.assertTrue(schedule_3 in schedules)
#
#    def test_between(self):
#        schedules, dates = Schedule.between(
#            datetime.datetime(2014, 1, 1), datetime.datetime(2014, 1, 2))
#        self.assertEqual(dates, [
#            [datetime.datetime(2014, 1, 1, 0, 0),
#             datetime.datetime(2014, 1, 2, 0, 0)],
#            [datetime.datetime(2014, 1, 1, 9, 0)],
#            [datetime.datetime(2014, 1, 1, 10, 0)]])
#
#    def test_between_live(self):
#        schedules, dates = Schedule.between(
#            datetime.datetime(2014, 1, 1),
#            datetime.datetime(2014, 1, 2),
#            live=True)
#        self.assertEqual(dates, [
#            [datetime.datetime(2014, 1, 1, 0, 0),
#             datetime.datetime(2014, 1, 2, 0, 0)],
#            [datetime.datetime(2014, 1, 1, 9, 0)]])
#
#    def test_between_schedule_board(self):
#        schedules, dates = Schedule.between(
#            datetime.datetime(2014, 1, 1),
#            datetime.datetime(2014, 1, 2),
#            schedule_board=self.schedule_board)
#        self.assertEqual(dates, [
#            [datetime.datetime(2014, 1, 1, 0, 0),
#             datetime.datetime(2014, 1, 2, 0, 0)],
#            [datetime.datetime(2014, 1, 1, 9, 0)],
#            [datetime.datetime(2014, 1, 1, 10, 0)]])
#
#    def test_between_emtpy_schedule_board(self):
#        schedules, dates = Schedule.between(
#            datetime.datetime(2014, 1, 1),
#            datetime.datetime(2014, 1, 2),
#            schedule_board=ScheduleBoard())
#        self.assertFalse(dates)
#
#    def test_between_exclude(self):
#        programme = Programme.objects.get(name="Programme 09:00 - 10:00")
#        schedule = Schedule.objects.get(
#            programme=programme,
#            day=WE,
#            start_hour=datetime.time(9, 0))
#        schedules, dates = Schedule.between(
#            datetime.datetime(2014, 1, 1),
#            datetime.datetime(2014, 1, 2),
#            exclude=schedule)
#        self.assertEqual(dates, [
#            [datetime.datetime(2014, 1, 1, 0, 0),
#             datetime.datetime(2014, 1, 2, 0, 0)],
#            [datetime.datetime(2014, 1, 1, 10, 0)]])
#
#    def test_schedule(self):
#        schedule, date = Schedule.schedule(datetime.datetime(2014, 1, 2, 4, 0))
#        self.assertEqual(schedule.programme.name, 'Programme 00:00 - 09:00')
#        self.assertEqual(schedule.start_hour, datetime.time(0, 0))
#        self.assertEqual(date, datetime.datetime(2014, 1, 2, 0, 0))
#
#    def test_schedule_silence(self):
#        schedule, date = Schedule.schedule(
#            datetime.datetime(2014, 1, 1, 14, 0))
#        self.assertIsNone(schedule)
#        self.assertIsNone(date)
#
#    def test_schedule_exclude(self):
#        programme = Programme.objects.get(name="Programme 00:00 - 09:00")
#        schedule = Schedule.objects.get(
#            programme=programme,
#            day=WE,
#            start_hour=datetime.time(0, 0))
#        schedule, date = Schedule.schedule(
#            datetime.datetime(2014, 1, 2, 4, 0),
#            exclude=schedule)
#        self.assertIsNone(schedule)
#        self.assertIsNone(date)
#
#    def test_get_next_date(self):
#        programme = Programme.objects.get(name="Programme 00:00 - 09:00")
#        schedule, date = Schedule.get_next_date(
#            programme, datetime.datetime(2014, 1, 2, 4, 0))
#        self.assertEqual(date, datetime.datetime(2014, 1, 3, 0, 0))
#
#    def test_get_next_date_no_schedule(self):
#        programme = Programme.objects.get(name="Programme 00:00 - 09:00")
#        schedule, date = Schedule.get_next_date(
#            programme, datetime.datetime(2014, 2, 1, 4, 0))
#        self.assertIsNone(date)
#
#    def test_get_next_date_no_current_board(self):
#        programme = Programme.objects.get(name="Programme 00:00 - 09:00")
#        schedule, date = Schedule.get_next_date(
#            programme, datetime.datetime(2013, 1, 2, 4, 0))
#        self.assertEqual(date, datetime.datetime(2014, 1, 1, 0, 0))


class ScheduleBoardManagerTests(TestDataMixin, TestCase):
    def setUp(self):
        super(ScheduleBoardManagerTests, self).setUp()
        self.manager = ScheduleBoardManager()

    def test_current(self):
        self.assertIsInstance(
            self.manager.current(datetime.date(2015, 1, 1)), ScheduleBoard)

    def test_current_no_date(self):
        self.assertIsInstance(self.manager.current(), ScheduleBoard)


class ScheduleBoardModelTests(TestCase):
    def setUp(self):
        self.board = ScheduleBoard.objects.create(
            name="january", start_date=datetime.datetime(2014, 1, 1), end_date=datetime.datetime(2014, 1, 31)
        )
        ScheduleBoard.objects.create(
            name="1_14_february", start_date=datetime.datetime(2014, 2, 1), end_date=datetime.datetime(2014, 2, 14)
        )
        ScheduleBoard.objects.create(
            name="after_14_february", start_date=datetime.datetime(2014, 2, 15))

        for schedule_board in ScheduleBoard.objects.all():
            schedule_board.clean()

    def test_runtime(self):
        january_board = ScheduleBoard.objects.get(name="january")
        february_board = ScheduleBoard.objects.get(name="1_14_february")
        after_board = ScheduleBoard.objects.get(name="after_14_february")

#        self.assertEqual(None, ScheduleBoard.get_current(datetime.datetime(2013, 12, 1, 0, 0, 0, 0)))
#        self.assertEqual(january_board, ScheduleBoard.get_current(datetime.datetime(2014, 1, 1, 0, 0, 0, 0)))
#        self.assertEqual(january_board, ScheduleBoard.get_current(datetime.datetime(2014, 1, 31, 0, 0, 0, 0)))
#        self.assertEqual(january_board, ScheduleBoard.get_current(datetime.datetime(2014, 1, 31, 12, 0, 0, 0)))
#        self.assertEqual(february_board, ScheduleBoard.get_current(datetime.datetime(2014, 2, 1, 0, 0, 0, 0)))
#        self.assertEqual(february_board, ScheduleBoard.get_current(datetime.datetime(2014, 2, 14, 0, 0, 0, 0)))
#        self.assertEqual(after_board, ScheduleBoard.get_current(datetime.datetime(2014, 2, 15, 0, 0, 0, 0)))
#        self.assertEqual(after_board, ScheduleBoard.get_current(datetime.datetime(2014, 6, 1, 0, 0, 0, 0)))

#    def test_end_before_start(self):
#        board = ScheduleBoard(
#            name="foo",
#            start_date=datetime.datetime(2014, 1, 31),
#            end_date=datetime.datetime(2014, 1, 1))
#        with self.assertRaises(ValidationError):
#            board.clean_fields()

#    def test_now_playing_1(self):
#        now_mock = datetime.datetime(2014, 1, 6, 0, 0, 0, 0)
#        schedule, date = Schedule.schedule(now_mock)
#        schedule_1 = Schedule.objects.get(programme=Programme.objects.get(name="Programme 00:00 - 09:00"), day=WE)
#        self.assertEqual(schedule_1, schedule)
#        self.assertEqual(datetime.datetime.combine(now_mock, schedule_1.start_hour), date)
#
#    def test_now_playing_2(self):
#        now_mock = datetime.datetime(2014, 1, 7, 0, 0, 0, 0)
#        schedule, date = Schedule.schedule(now_mock)
#        schedule_1 = Schedule.objects.get(programme=Programme.objects.get(name="Programme 00:00 - 09:00"), day=WE)
#        self.assertEqual(schedule_1, schedule)
#        self.assertEqual(datetime.datetime.combine(now_mock, schedule_1.start_hour), date)
#
    def test_str(self):
        self.assertEqual(str(self.board), "january")


#class ScheduleViewTests(TestCase):
#    def setUp(self):
#        admin = User.objects.create_user(
#            username='admin', password='topsecret')
#        admin.user_permissions.add(
#            Permission.objects.get(codename='change_schedule'))
#
#        schedule_board = ScheduleBoard.objects.create(
#            name='Board',
#            start_date=datetime.datetime(2014, 1, 1, 0, 0, 0, 0))
#
#        programme = Programme.objects.create(
#            name="Test-Programme", current_season=1, runtime=540,
#            start_date=datetime.datetime(2014, 1, 1, 0, 0, 0, 0))
#
#        self.schedule = Schedule.objects.create(
#            schedule_board=schedule_board, programme=programme,
#            start_hour=datetime.time(0, 0, 0), day=WE, type='L')
#


class ScheduleUtilsTests(TestDataMixin, TestCase):
    def test_available_dates_after(self):
        Schedule.objects.create(
            programme=self.programme,
            schedule_board=self.schedule_board,
            type="L",
            recurrences= recurrence.Recurrence(
                dtstart=datetime.datetime(2015, 1, 6, 16, 0, 0),
                dtend=datetime.datetime(2015, 1, 31, 16, 0, 0),
                rrules=[recurrence.Rule(recurrence.WEEKLY)]))

        dates = utils.available_dates(
            self.programme, datetime.datetime(2015, 1, 5))
        self.assertEqual(dates.next(), datetime.datetime(2015, 1, 5, 14, 0))
        self.assertEqual(dates.next(), datetime.datetime(2015, 1, 6, 14, 0))
        self.assertEqual(dates.next(), datetime.datetime(2015, 1, 6, 16, 0))

    def test_available_dates_none(self):
        dates = utils.available_dates(Programme(), datetime.datetime.now())
        with self.assertRaises(StopIteration):
            dates.next()

    def test_rearrenge_episodes(self):
        utils.rearrange_episodes(self.programme, datetime.datetime(2015, 1, 1))
        self.assertListEqual(
            map(lambda e: e.issue_date, self.programme.episode_set.all()[:5]),
            [datetime.datetime(2015, 1, 1, 14, 0),
             datetime.datetime(2015, 1, 2, 14, 0),
             datetime.datetime(2015, 1, 3, 14, 0),
             datetime.datetime(2015, 1, 4, 14, 0),
             datetime.datetime(2015, 1, 5, 14, 0)])

    def test_rearrenge_episodes_new_schedule(self):
        Schedule.objects.create(
            programme=self.programme,
            schedule_board=ScheduleBoard.objects.create(),
            type="L",
            recurrences= recurrence.Recurrence(
                dtstart=datetime.datetime(2015, 1, 3, 16, 0, 0),
                dtend=datetime.datetime(2015, 1, 31, 16, 0, 0),
                rrules=[recurrence.Rule(recurrence.WEEKLY)]))

        utils.rearrange_episodes(self.programme, datetime.datetime(2015, 1, 1))
        self.assertListEqual(
            map(lambda e: e.issue_date, self.programme.episode_set.all()[:5]),
            [datetime.datetime(2015, 1, 1, 14, 0),
             datetime.datetime(2015, 1, 2, 14, 0),
             datetime.datetime(2015, 1, 3, 14, 0),
             datetime.datetime(2015, 1, 3, 16, 0),
             datetime.datetime(2015, 1, 4, 14, 0)])
