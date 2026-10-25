# 信頼済み commit からの描画で、入力ファイル・Chart・設定値の境界を守ることを確認します。
"""Synthetic fixed-inventory tests; no deployment or release authorization."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('render',Path(__file__).with_name('trusted-render.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def files(charts):
    return {path:{} for chart in charts for path in ('charts/'+chart+'/Chart.yaml','environments/local/'+chart+'.yaml')}

class Inventory(unittest.TestCase):
    def test_combined_entries_keeps_current_profile_separate(self):
        render={'inventory':{'entries':[{'namespace':'account'}]},
                'currentAccountInventory':{'entries':[{'namespace':'learning-account-release'}]}}
        self.assertEqual([x['namespace'] for x in m.combined_entries(render)],
                         ['account','learning-account-release'])
    def test_baseline_absence_and_complete_additions(self):
        baseline=m.chart_inventory(files(m.ACTIVE_CHARTS))
        self.assertEqual(baseline,list(m.ACTIVE_CHARTS))
        for chart in m.INACTIVE_CHARTS:
            self.assertIn(chart,m.chart_inventory(files((*baseline,chart)),baseline))
    def test_missing_unknown_and_removal(self):
        baseline=files((*m.ACTIVE_CHARTS,*m.INACTIVE_CHARTS))
        cases=[files(('backend','postgresql')), {**baseline,'charts/unknown/Chart.yaml':{}},
               {**baseline,'environments/local/unknown.yaml':{}},
               {k:v for k,v in baseline.items() if k!='charts/order/Chart.yaml'},
               {k:v for k,v in baseline.items() if k!='environments/local/order-postgresql.yaml'}]
        for case in cases:
            with self.assertRaises(ValueError):m.chart_inventory(case)
        with self.assertRaises(ValueError):m.chart_inventory(files(m.ACTIVE_CHARTS),list(m.ACTIVE_CHARTS)+['order'])
    def test_pr_policy_is_not_an_input(self):
        values=files(m.ACTIVE_CHARTS)
        values['charts/order/Chart.yaml']={}
        values['ci/inventory.json']={'active':list(m.ACTIVE_CHARTS)}
        with self.assertRaises(ValueError):m.chart_inventory(values)
    def test_shell_inventory_matches_trusted_constants(self):
        script=Path(__file__).with_name('validate.sh').read_text()
        for chart in m.ACTIVE_CHARTS+m.INACTIVE_CHARTS:
            self.assertIn('charts/'+chart+'/',script)
            self.assertIn('environments/local/'+chart+'.yaml',script)

if __name__=='__main__':unittest.main()
