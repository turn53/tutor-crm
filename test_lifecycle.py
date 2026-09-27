import unittest, tempfile, datetime
from pathlib import Path
import app

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();app.DB=Path(self.tmp.name)/'test.db';app.seed()
    def tearDown(self):self.tmp.cleanup()
    def get(self,kind,id):return next(x for x in app.all_data()[kind] if x['id']==id)
    def test_delete_restore_payment_recalculates(self):
        app.trash('payments','p1');m=app.metrics(app.all_data());self.assertEqual(m['paid'],25000);self.assertEqual(m['debt'],10600)
        app.trash('payments','p1',True);self.assertEqual(app.metrics(app.all_data())['paid'],28000)
    def test_student_deletion_preserves_money_but_removes_forecast(self):
        app.trash('students','anna');m=app.metrics(app.all_data(),datetime.date(2026,9,15));self.assertEqual(m['paid'],28000)
        self.assertEqual(next(s for s in m['students'] if s['id']=='anna')['forecast'],0)
        app.trash('students','anna',True);self.assertEqual(app.metrics(app.all_data(),datetime.date(2026,9,15))['forecast'],712000)
    def test_lesson_delete_restores_statistics_and_prices(self):
        app.trash('lessons','l1');self.assertEqual(app.metrics(app.all_data())['hours'],1.5)
        e=self.get('enrollments','e1');e['hourly']=3000;app.save('enrollments',e)
        app.trash('lessons','l1',True);self.assertEqual(self.get('lessons','l1')['charges'][0]['amount'],1000)
    def test_restore_bill_duplicate_rejected(self):
        b=app.all_data()['bills'][0];app.trash('bills',b['id']);copy={**b};copy.pop('id');app.save('bills',copy)
        with self.assertRaises(ValueError):app.trash('bills',b['id'],True)
        self.assertTrue(self.get('bills',b['id'])['deleted'])
    def test_group_membership_saves_together_preserves_past(self):
        g=self.get('groups','ege');app.save_group({'group':{**g,'hourly':1400},'members':[{'student':'anna','hourly':1400,'monthly':11200,'until':'2028-05-31'}]})
        self.assertFalse(self.get('enrollments','e2')['active']);self.assertEqual(self.get('enrollments','e1')['hourly'],1400)
        self.assertEqual(sum(c['amount'] for c in self.get('lessons','l1')['charges']),2000)
        app.save('lessons',{'group':'ege','student':'','date':'2026-09-16','minutes':60})
        self.assertEqual(len(app.all_data()['lessons'][-1]['charges']),1)
    def test_group_invalid_roster_rolls_back_all_changes(self):
        old=self.get('groups','ege')
        with self.assertRaises(ValueError):app.save_group({'group':{**old,'name':'Changed'},'members':[{'student':'missing','hourly':1000,'monthly':8000}]})
        self.assertEqual(self.get('groups','ege')['name'],old['name']);self.assertTrue(self.get('enrollments','e1')['active'])
    def test_restore_enrollment_conflict(self):
        e=self.get('enrollments','e1');app.trash('enrollments','e1');e.pop('id');app.save('enrollments',e)
        with self.assertRaises(ValueError):app.trash('enrollments','e1',True)
    def test_deleted_group_does_not_forecast(self):
        app.trash('groups','ege');m=app.metrics(app.all_data(),datetime.date(2026,9,15));self.assertEqual(m['forecast'],392000);self.assertEqual(m['paid'],28000)
    def test_extended_profile_fields_survive_edit(self):
        s=self.get('students','anna');s.update(parent_name='Ирина',parent_contact='Тестовый контакт',goal='ЕГЭ',grade='10');app.save('students',s)
        self.assertEqual(self.get('students','anna')['parent_contact'],'Тестовый контакт')

if __name__=='__main__':unittest.main()
