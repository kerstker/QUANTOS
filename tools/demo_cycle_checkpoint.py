"""Root-owned append-only attempt checkpoints for one installed Demo cycle.

These are dispatch barriers, not acknowledgments or permission to retry a task.
The original task claims, issuer allowance, OMS and risk gates remain in force.
"""
import fcntl
import os
from contextlib import contextmanager

from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import demo_supervisor as supervisor
from tools.demo_operator_setup import canonical, digest
from tools import demo_operating_bundle as bundle


@contextmanager
def cycle_lock(area,*,maintenance=False):
    # Lock the existing private directory inode: no persistent lock file, no
    # unlocked remove/recreate race and automatic release on process death.
    with supervisor.controller_lease(maintenance=maintenance):
        supervisor._root_private_directory(area)
        fd = os.open(area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('FUNCTIONAL_CYCLE_BUSY') from None
            yield
        finally: os.close(fd)


def read(area, *, cycle, source_commit, opening_task):
    directory = area/'attempts'
    if not directory.exists(): return None
    supervisor._root_private_directory(directory)
    paths = sorted(directory.iterdir())
    require(len(paths) <= 3, 'FUNCTIONAL_CHECKPOINT_BUDGET')
    previous = None
    attempts = []
    for index, path in enumerate(paths):
        require(path.name == str(index)+'.json', 'FUNCTIONAL_CHECKPOINT_SEQUENCE')
        value = load_json_without_duplicate_keys(supervisor._read_root_owned_json(path))
        require(type(value) is dict and set(value) == {
            'schema','cycle_sha256','source_commit','index','previous_sha256','task','task_sha256'} and
            value['schema'] == 'DEMO_CYCLE_ATTEMPT_V1' and value['cycle_sha256'] == cycle and
            value['source_commit'] == source_commit and value['index'] == index and
            value['previous_sha256'] == previous and value['task_sha256'] == digest(value['task']),
            'FUNCTIONAL_CHECKPOINT_BINDING')
        attempts.append(value['task']); previous = digest(value)
    require(not attempts or attempts[0] == opening_task, 'FUNCTIONAL_CHECKPOINT_OPENING')
    require(len({digest(t) for t in attempts}) == len(attempts), 'FUNCTIONAL_CHECKPOINT_DUPLICATE')
    return {'schema':'DEMO_CYCLE_CONTINUATION_V1','attempts':attempts}


def append(area, state, *, cycle, source_commit, opening_task):
    require(state.get('schema') == 'DEMO_CYCLE_CONTINUATION_V1' and
        type(state.get('attempts')) is list, 'FUNCTIONAL_CHECKPOINT_STATE')
    directory = area/'attempts'; directory.mkdir(mode=0o700, exist_ok=True)
    supervisor._root_private_directory(directory)
    prior = read(area, cycle=cycle, source_commit=source_commit, opening_task=opening_task)
    old = prior['attempts'] if prior else []
    current = state['attempts']
    require(1 <= len(current) <= 3 and len(current) == len(old)+1 and current[:-1] == old and
        current[0] == opening_task, 'FUNCTIONAL_CHECKPOINT_APPEND_ONLY')
    index = len(old)
    previous = load_json_without_duplicate_keys(supervisor._read_root_owned_json(directory/(str(index-1)+'.json'))) if index else None
    value = {'schema':'DEMO_CYCLE_ATTEMPT_V1','cycle_sha256':cycle,'source_commit':source_commit,
        'index':index,'previous_sha256':digest(previous) if previous else None,
        'task':current[-1],'task_sha256':digest(current[-1])}
    bundle.write_new(directory/(str(index)+'.json'), canonical(value))


def legacy_attempts(*, opening_task, source_commit):
    """Recover only actual root task claims; never infer a send from a UI phase."""
    directory = supervisor.STATE/'task-claims'
    supervisor._root_private_directory(directory)
    client = opening_task['orders'][0]['newClientOrderId']
    require(client.endswith('open'), 'FUNCTIONAL_LEGACY_CLIENT')
    clients = [client]+[client[:-4]+'close'+str(i) for i in (1,2)]
    found = {}
    paths = list(directory.iterdir())
    require(len(paths) <= 16000, 'FUNCTIONAL_LEGACY_BUDGET')
    for path in paths:
        value = load_json_without_duplicate_keys(supervisor._read_root_owned_json(path))
        require(value.get('schema') == 'DEMO_TASK_CLAIM_V1' and
            path.name == value.get('task_sha256','')+'.json', 'FUNCTIONAL_LEGACY_CLAIM')
        if value['source_commit'] != source_commit: continue
        task = load_json_without_duplicate_keys(supervisor.read_document(supervisor.STATE/'tasks',value['task_sha256']))
        require(digest(task) == value['task_sha256'], 'FUNCTIONAL_LEGACY_TASK')
        if len(task.get('orders',[])) != 1: continue
        order = task['orders'][0]
        if order['newClientOrderId'] not in clients: continue
        index = clients.index(order['newClientOrderId'])
        require(index not in found and all(task[k] == opening_task[k] for k in opening_task if k != 'orders'),
            'FUNCTIONAL_LEGACY_SCOPE')
        require(index == 0 and task == opening_task or index > 0 and order['side'] == 'SELL' and
            order['reduceOnly'] is True and order['symbol'] == 'BTCUSDC', 'FUNCTIONAL_LEGACY_ORDER')
        found[index] = task
    require(found and sorted(found) == list(range(len(found))), 'FUNCTIONAL_LEGACY_ATTEMPTS_REQUIRED')
    return {'schema':'DEMO_CYCLE_CONTINUATION_V1','attempts':[found[i] for i in range(len(found))]}
