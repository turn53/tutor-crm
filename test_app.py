import unittest, tempfile, datetime
from pathlib import Path
import app

class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); app.DB=Path(self.tmp.name)/'test.db'; app.seed()
    def tearDown(self): self.tmp.cleanup()
    def test_partial_payment_and_refund(self):
        d=app.all_data(); m=app.metrics(d)
        self.assertEqual(m['paid'],28000)
        self.assertEqual(m['debt'],7600)
        app.save('payments',{'student':'boris','amount':1000,'date':'2026-09-15','period':datetime.date.today().isoformat()[:7],'operation':'Возврат'})
        m=app.metrics(app.all_data());self.assertEqual(m['paid'],27000);self.assertEqual(m['debt'],8600)
    def test_group_hour_not_counted_per_student(self):
        m=app.metrics(app.all_data());self.assertEqual(m['hours'],2.5);self.assertEqual(m['hourly'],2000)
        self.assertEqual(next(s for s in m['students'] if s['id']=='anna')['hourly'],1000)
    def test_forecast_includes_two_formats(self):
        m=app.metrics(app.all_data(),datetime.date(2026,9,15))
        self.assertEqual(next(s for s in m['students'] if s['id']=='anna')['forecast'],11600*20)
    def test_old_lesson_prices_do_not_change(self):
        d=app.all_data(); e=next(x for x in d['enrollments'] if x['id']=='e1');e['hourly']=3000;app.save('enrollments',e)
        self.assertEqual(app.metrics(app.all_data())['hourly'],2000)
    def test_invalid_money_and_duplicate_bill(self):
        with self.assertRaises(ValueError): app.money('NaN')
        b=app.all_data()['bills'][0];b.pop('id')
        with self.assertRaises(ValueError): app.save('bills',b)
    def test_report_edit_preserves_charges(self):
        l=app.all_data()['lessons'][0];l['notes']='Новое объяснение';app.save('lessons',l)
        self.assertEqual(len(next(x for x in app.all_data()['lessons'] if x['id']==l['id'])['charges']),2)

if __name__=='__main__': unittest.main()
