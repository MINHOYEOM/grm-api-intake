import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, Mock

import collect_rum_referrer_paths as cross

START = '2026-09-19T00:00:00Z'
END = '2026-09-19T23:59:59Z'


def payload(rows):
    return {'data': {'viewer': {'accounts': [{'rows': rows}]}}}


def row(host, path, visits, si=1):
    return {'dimensions': {'date': START[:10], 'refererHost': host, 'requestPath': path},
            'sum': {'visits': visits}, 'avg': {'sampleInterval': si}}


class CrossTest(unittest.TestCase):
    def test_joint_query_preserves_population_and_visits(self):
        q = cross.build_query(2)
        for text in ('bot: 0', 'date refererHost requestPath', 'sum { visits }',
                     'avg { sampleInterval }', 'limit: 2', 'sum_visits_DESC'):
            self.assertIn(text, q)
        self.assertNotIn('\n        count', q)

    def test_pairs_not_cartesian_and_private_query_removed(self):
        rows, run = cross.parse_response(payload([
            row('search.naver.com', '/a/?key=private', 3),
            row('search.naver.com', '/a/#fragment', 2), row('google.com', '/b/', 4)]), START, END)
        self.assertEqual([(r['referer_host'], r['request_path'], r['visits']) for r in rows],
                         [('search.naver.com', '/a/', 5), ('google.com', '/b/', 4)])
        self.assertNotIn('private', json.dumps(rows))
        self.assertFalse(run['api_limit_hit'])

    def test_low_limits_detect_both_types_of_loss_and_tie_break(self):
        source = [row('b', '/b/', 1), row('a', '/a/', 1)]
        rows, run = cross.parse_response(payload(source), START, END, api_limit=2, day_cap=1)
        self.assertEqual(rows[0]['referer_host'], 'a')
        self.assertTrue(run['api_limit_hit'])
        self.assertTrue(run['day_cap_hit'])
        self.assertEqual(run['dropped_pairs'], 1)
        self.assertEqual((rows, run), cross.parse_response(payload(source[::-1]), START, END, 2, 1))

    def test_missing_response_does_not_become_empty_day(self):
        for p in ({}, {'errors': [{'message': 'private'}]}, payload([{}])):
            with self.assertRaises(ValueError): cross.parse_response(p, START, END)
        rows, run = cross.parse_response(payload([]), START, END)
        self.assertEqual(rows, [])
        self.assertEqual(run['retained_rows'], 0)

    def test_precision_and_completeness_ratchets(self):
        _, old = cross.parse_response(payload([row('h', '/', 1)]), START, END)
        new = dict(old, sample_interval=10)
        self.assertFalse(cross.should_replace(new, old))
        self.assertTrue(cross.should_replace(new, old, True))
        self.assertFalse(cross.should_replace(dict(old, api_limit_hit=True), old, True))
        self.assertFalse(cross.should_replace(dict(old, window_end=START), old))

    def test_utc_days_are_disjoint_and_keep_final_partial_day(self):
        windows = list(cross.day_windows(START, '2026-09-23T04:00:00Z'))
        self.assertEqual(len(windows), 5)
        self.assertEqual(windows[0], (START, END))
        self.assertEqual(windows[-1][1], '2026-09-23T04:00:00Z')
        for (a,b),(c,d) in zip(windows,windows[1:]): self.assertLess(b,c)

    def test_dry_run_logs_no_values_and_never_saves(self):
        response = Mock(status_code=200)
        response.json.return_value = payload([row('private.example', '/private/', 978)])
        env = {'CLOUDFLARE_ANALYTICS_TOKEN':'token','CLOUDFLARE_ACCOUNT_ID':'acct','CLOUDFLARE_RUM_SITE_TAG':'site'}
        output = io.StringIO()
        with patch.dict('os.environ',env), patch('requests.post',return_value=response), \
             patch.object(cross,'save') as save, redirect_stdout(output):
            cross.main(['--start',START,'--end',END,'--dry-run','--api-limit','1'])
        save.assert_not_called()
        self.assertIn('api_limit_hit=True',output.getvalue())
        for value in ['private', '978', 'token']:self.assertNotIn(value,output.getvalue())


if __name__ == '__main__': unittest.main()
