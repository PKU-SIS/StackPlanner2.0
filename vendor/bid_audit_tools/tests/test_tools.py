import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal

from bid_audit.core import Context, dump, contained, norm
from bid_audit.price_rules import money, metrics, m14, m15
from bid_audit.text_rules import m01, m02, m03, m04, m07, m08, m09, Template, numeric_deviation
from bid_audit.pdf_rules import cdc, parse_pdf_date, m06, m10, m11, m12
from bid_audit.runner import run_rule, read_result


class Numbers(unittest.TestCase):
    def test_money_exact(self):self.assertEqual(money('18,960,000.00'),1896000000)
    def test_bad_money(self):
        for value in ['1.001','1e3','1,00','NaN','-1']:
            with self.subTest(value=value),self.assertRaises(ValueError):money(value)
    def test_report_metrics(self):
        r=metrics([1890000000,1896000000,1898000000])
        import statistics
        prices=[18900000,18960000,18980000]
        self.assertAlmostEqual(float(r['population_cv_percent']),statistics.pstdev(prices)/statistics.mean(prices)*100,places=12)
        self.assertEqual(r['gap_multiples'],[3,1]);self.assertFalse(r['strict_positive_arithmetic_progression'])
    def test_equal_prices(self):
        r=metrics([100,100,100]);self.assertTrue(r['all_equal']);self.assertIsNone(r['gap_multiples'])
    def test_two_prices_not_progression(self):self.assertFalse(metrics([100,200])['strict_positive_arithmetic_progression'])
    def test_insufficient(self):
        with self.assertRaises(ValueError):metrics([100])
    def test_numeric_bad(self):self.assertTrue(numeric_deviation('直径≤5mm','直径6mm')['unfavorable'])
    def test_unit_conversion(self):self.assertFalse(numeric_deviation('直径≥10mm','直径1cm')['unfavorable'])
    def test_tolerance_is_unknown(self):self.assertIsNone(numeric_deviation('波长≥410±5nm','波长410nm'))
    def test_approximate_is_unknown(self):self.assertIsNone(numeric_deviation('寿命≥12000小时','寿命约14400小时'))
    def test_multi_quantity_unknown(self):self.assertIsNone(numeric_deviation('直径≥5mm且长度≥2cm','直径6mm长度3cm'))
    def test_date_timezones(self):self.assertIsNotNone(parse_pdf_date("D:20260101120000+08'00'"))
    def test_short_date_unknown(self):self.assertIsNone(parse_pdf_date('D:202601'))
    def test_cdc_insert_resilience(self):
        import random
        data=random.Random(42).randbytes(100000)
        a={chunk for _,chunk in cdc(data)};b={chunk for _,chunk in cdc(b'inserted-prefix'+data)}
        self.assertGreater(len(a&b)/len(a|b),.8)


class TextRules(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def ctx(self, texts, **extra):
        docs=[]
        for i,(role,bidder,text) in enumerate(texts):
            p=self.root/f'{i}.txt';p.write_text(text)
            docs.append({'id':str(i),'path':p.name,'role':role,**({'bidder':bidder} if bidder else {})})
        dump(self.root/'manifest.json',{'schema_version':'1.0','project_id':'synthetic','documents':docs,**extra})
        return Context(self.root/'manifest.json',self.root/'cache')
    def test_template_removed_but_custom_retained(self):
        template='All bidders must comply with the published standard purchase terms and conditions.'
        custom='Our specially developed maintenance protocol includes a unique seven-stage inspection sequence.'
        c=self.ctx([('tender',None,template),('bid','A',template+'。'+custom),('bid','B',template+'。'+custom)])
        r=m01(c);self.assertTrue(r['findings']);self.assertTrue(all('publishedstandard' not in x['text'] for x in r['findings']))
    def test_template_only_has_no_match(self):
        t='This is a sufficiently long public template clause for every bidder in the same tender.'
        c=self.ctx([('tender',None,t),('bid','A',t),('bid','B',t)])
        self.assertEqual(m01(c)['findings'],[])
    def test_actor_substitution(self):
        t='将在设备抵达现场之后提供完整且明确的安装测试和培训实施计划'
        c=self.ctx([('bid','A','我司'+t),('bid','B','本公司'+t)])
        self.assertEqual(len(m02(c)['findings']),1)
    def test_punctuation_double_semicolon(self):
        t='本方案独有的设备维护步骤和责任分配安排；；双方在现场确认之后共同执行'
        c=self.ctx([('bid','A',t),('bid','B',t)])
        self.assertTrue(m08(c)['findings'])
    def test_entity_overlap(self):
        c=self.ctx([('bid','A','联系手机号13812345678。'),('bid','B','服务手机13812345678。')])
        self.assertTrue(m09(c)['findings'])
    def test_rounds_not_mixed(self):
        t='This is a long independently written sentence repeated in two separate rounds only.'
        c=self.ctx([('bid','A',t),('bid','B',t)])
        c.docs[1]['round']='2'
        self.assertEqual(m01(c)['findings'],[])
    def test_missing_source_rejected(self):
        c=self.ctx([('bid','A','actual text'),('bid','B','actual text')])
        with self.assertRaises(ValueError):c.verify_quote({'document_id':'0','page':None,'quote':'invented text'})
    def test_traversal_rejected(self):
        with self.assertRaises(ValueError):contained(self.root/'../outside',self.root)
    def test_one_bidder_insufficient(self):
        c=self.ctx([('bid','A','some content')]);r=run_rule('M01',c.manifest_path,self.root/'results')
        self.assertEqual(r['status'],'insufficient_data')
    def test_failed_run_not_zero(self):
        c=self.ctx([('bid','A','some content')]);m=c.manifest;m['documents'][0]['path']='missing.pdf';dump(c.manifest_path,m)
        r=run_rule('M01',c.manifest_path,self.root/'results')
        self.assertEqual(r['status'],'error');self.assertIsNone(r['finding_count'])
    def test_result_paging(self):
        t='Special customized operational inspection steps shared by these two vendors only.'
        c=self.ctx([('bid','A',t),('bid','B',t)])
        r=run_rule('M01',c.manifest_path,self.root/'results')
        page=read_result(r['result_directory'],'evidence',0,1)
        self.assertEqual(len(page['items']),1);self.assertEqual(page['next_offset'],1)
    def test_pdf_rule_on_text_is_insufficient(self):
        c=self.ctx([('bid','A','First text'),('bid','B','Second text')])
        self.assertEqual(run_rule('M11',c.manifest_path,self.root/'results')['status'],'insufficient_data')


class StructuredRules(unittest.TestCase):
    def rows(self):
        return [{'bidder':b,'round':'1','lot':'1','requirement':'设备插入部分的直径必须≤5mm',
                 'response':'设备插入部分的直径为6mm','declared_deviation':'无偏离',
                 'evidence':[{'document_id':b,'page':1}]} for b in ['A','B']]
    class C:
        params={};manifest={}
        def __init__(self):self.warnings=[]
    def test_ambiguous_requirement_not_cross_joined(self):
        rows=self.rows();rows.append(copy.deepcopy(rows[0]))
        with patch('bid_audit.text_rules.response_rows',return_value=rows):
            c=self.C();self.assertFalse(m03(c)['findings']);self.assertTrue(c.warnings)
    def test_m03_response_pairs(self):
        with patch('bid_audit.text_rules.response_rows',return_value=self.rows()):
            self.assertEqual(len(m03(self.C())['findings']),1)
    def test_m04_negative_despite_declared_no(self):
        with patch('bid_audit.text_rules.response_rows',return_value=self.rows()):
            self.assertEqual(len(m04(self.C())['findings']),1)
    def test_m07_common_change_is_candidate(self):
        with patch('bid_audit.text_rules.response_rows',return_value=self.rows()):
            self.assertTrue(all('candidate' in r['kind'] for r in m07(self.C())['findings']))
    def quote(self,bidder,prices,round_id='1'):
        return {'bidder':bidder,'round':round_id,'lot':'1','currency':'CNY','source_project_id':'P',
                'total_cents':sum(prices),'total_yuan':str(sum(prices)/100),'opening_evidence':{},
                'items':[{'item_id':str(i),'product':f'Product {i}','unit':'台','model':'A','quantity':'1',
                          'unit_price_cents':v,'evidence':{}} for i,v in enumerate(prices)]}
    def test_m15_exact_list(self):
        with patch('bid_audit.price_rules.quotes',return_value=[self.quote('A',[100,200]),self.quote('B',[100,200])]):
            r=m15(self.C());self.assertTrue(r['findings'][0]['all_aligned_prices_equal'])
    def test_m15_partial_not_all(self):
        with patch('bid_audit.price_rules.quotes',return_value=[self.quote('A',[100,200]),self.quote('B',[100,300])]):
            r=m15(self.C());self.assertFalse(r['findings'][0]['all_aligned_prices_equal']);self.assertEqual(r['findings'][0]['m'],1)
    def test_m15_one_line_not_applicable(self):
        with patch('bid_audit.price_rules.quotes',return_value=[self.quote('A',[100]),self.quote('B',[100])]):
            self.assertEqual(m15(self.C())['status'],'not_applicable')
    def test_m15_permuted_rows_align(self):
        a=self.quote('A',[100,200]);b=self.quote('B',[100,200]);b['items'].reverse()
        with patch('bid_audit.price_rules.quotes',return_value=[a,b]):self.assertTrue(m15(self.C())['findings'][0]['all_aligned_prices_equal'])
    def test_m14_stable(self):
        with patch('bid_audit.price_rules.quotes',return_value=[self.quote('A',[100],'1'),self.quote('A',[100],'2')]):
            self.assertTrue(m14(self.C())['findings'][0]['unchanged'])
    def test_m14_changed_product_incomparable(self):
        a=self.quote('A',[100],'1');b=self.quote('A',[100],'2');b['items'][0]['model']='Other'
        with patch('bid_audit.price_rules.quotes',return_value=[a,b]):
            self.assertEqual(m14(self.C())['status'],'insufficient_data')


class PdfRules(unittest.TestCase):
    def test_identical_image_and_stream_positive_controls(self):
        from PIL import Image,ImageDraw
        from reportlab.pdfgen.canvas import Canvas
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);im=Image.new('RGB',(200,200),'white');dr=ImageDraw.Draw(im)
            dr.rectangle((20,30,170,160),fill='black');im.save(root/'asset.png')
            docs=[]
            for b in ['A','B']:
                f=root/(b+'.pdf');c=Canvas(str(f));c.setAuthor('Controlled test author')
                c.drawString(20,800,'Synthetic positive control image and content stream')
                c.drawImage(str(root/'asset.png'),20,400,width=200,height=200);c.save()
                docs.append({'id':b,'path':f.name,'role':'bid','bidder':b})
            dump(root/'manifest.json',{'schema_version':'1.0','project_id':'synthetic','documents':docs})
            ctx=Context(root/'manifest.json',root/'cache')
            self.assertTrue(m06(ctx)['findings']);self.assertTrue(m10(ctx)['findings'])
            self.assertTrue(m11(ctx)['findings']);self.assertTrue(m12(ctx)['findings'])

if __name__=='__main__':unittest.main()
