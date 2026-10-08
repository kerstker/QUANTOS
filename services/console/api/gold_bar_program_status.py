"""Bounded operational projection; recorded stages do not prove process liveness."""
import os

from services.console.api.gold_input_catalog import _directory, _read
from services.governance.json_integrity import load_json_without_duplicate_keys as decode


STAGES = {'WINDOW_ADVANCED', 'USING_RETAINED_HYPOTHESIS', 'PROPOSING_HYPOTHESIS',
          'WAITING_CLOSED_BAR', 'COLLECTING', 'RESEARCH_SUBMITTED', 'RESEARCH_FAILED',
          'RESEARCH_RECOVERY_SUBMITTED', 'REVIEW_SUBMITTED', 'REVIEW_FAILED',
          'ROUND_COMPLETE', 'PROGRAM_LOSS_STOP'}
TERMINALS = {'RESEARCH_PROGRAM_COMPLETE', 'RESEARCH_PROGRAM_INCOMPLETE',
             'RESEARCH_PROGRAM_STOPPED_LOSS_LIMIT'}


def programs(root):
    result = dict(status='NO_SNAPSHOT', items=[], errors=[], truncated=False)
    try:
        parent = _directory(root/'data/cache/gold_bar_research/programs')
    except FileNotFoundError:
        return result
    except (OSError, ValueError):
        return {**result, 'status': 'DATA_BLOCKED', 'errors': ['PROGRAM_DIRECTORY_UNAVAILABLE']}
    budget = 8_000_000
    examined = 0
    try:
        for ident in range(1000, 0, -1):
            if examined >= 16 or budget < 2_300_000:
                result['truncated'] = True
                break
            try:
                directory = os.open(str(ident), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            except FileNotFoundError:
                continue
            except OSError:
                result['errors'].append(f'PROGRAM_{ident}_UNAVAILABLE')
                examined += 1
                continue
            examined += 1
            try:
                plan_raw = _read(directory, 'plan.json', 32768)
                plan = decode(plan_raw)
                if (plan.get('schema') != 'GOLD_BAR_PROGRAM_V1' or type(plan.get('program_id')) is not int
                        or plan.get('program_id') != ident
                        or plan.get('submission_enabled') is not False
                        or type(plan.get('rounds')) is not int or not 1 <= plan['rounds'] <= 8
                        or type(plan.get('window_bars')) is not int or not 1 <= plan['window_bars'] <= 4):
                    raise ValueError('invalid program plan')
                try:
                    raw = _read(directory, 'events.jsonl', 262144)
                except FileNotFoundError:
                    raw = b''
                budget -= len(plan_raw) + len(raw)
                # A live append can leave an incomplete final JSON line.
                events = [decode(line) for line in raw.split(b'\n')[:-1] if line]
                previous = -1
                for event in events:
                    at = event.get('observed_at_ms')
                    if (event.get('status') not in STAGES or event.get('submission_enabled') is not False
                            or type(at) is not int or at < previous
                            or type(event.get('attempt')) is not int or event['attempt'] < 1):
                        raise ValueError('invalid event')
                    previous = at
                last = events[-1] if events else {}
                row = dict(program_id=ident, planned_rounds=plan['rounds'], window_bars=plan['window_bars'],
                    completed_rounds=len({e['attempt'] for e in events if e['status'] == 'ROUND_COMPLETE'}),
                    recorded_stage=last.get('status'), recorded_at_ms=last.get('observed_at_ms'),
                    terminal_status=None, error_type=None, liveness='UNKNOWN',
                    economic_eligible=False, submission_enabled=False)
                if row['completed_rounds'] > plan['rounds']:
                    raise ValueError('invalid completed count')
                try:
                    raw = _read(directory, 'summary.json', 2_000_000)
                except FileNotFoundError:
                    raw = None
                if raw is not None:
                    budget -= len(raw)
                    summary = decode(raw)
                    if (summary.get('status') not in TERMINALS
                            or any(summary.get(k) is not False for k in
                                   ('authoritative', 'economic_eligible', 'submission_enabled'))
                            or type(summary.get('rounds')) is not list
                            or not row['completed_rounds'] <= len(summary['rounds']) <= plan['rounds']):
                        raise ValueError('invalid terminal')
                    if summary['status'] == 'RESEARCH_PROGRAM_COMPLETE' and len(summary['rounds']) != plan['rounds']:
                        raise ValueError('incomplete claimed completion')
                    error = summary.get('error_type')
                    if error is not None and (type(error) is not str or not error.isidentifier() or len(error) > 100):
                        raise ValueError('invalid error type')
                    row.update(terminal_status=summary['status'], error_type=error,
                               completed_rounds=len(summary['rounds']))
                if _read(directory, 'plan.json', 32768) != plan_raw:
                    raise ValueError('changed plan')
                result['items'].append(row)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                result['errors'].append(f'PROGRAM_{ident}_UNAVAILABLE')
            finally:
                os.close(directory)
    finally:
        os.close(parent)
    result['status'] = 'DATA_BLOCKED' if result['errors'] else 'AVAILABLE' if result['items'] else 'NO_SNAPSHOT'
    return result
