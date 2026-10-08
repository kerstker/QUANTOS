"""First received right-boundary decisions, without selecting on signal outcomes."""
from services.research.gold_trade_windows import MAX_EVENTS, WindowError, build_window


def watermark_decisions(document, *, cursor_factory):
    start=document['start_ms'];rule=document['rule'];count=len(document['decision_times_ms'])
    width=rule['window_ms'];age=rule['max_age_ms'];packets=document['packets']
    if (type(start) is not int or start<0 or type(width) is not int or width<=0
            or type(age) is not int or age<=0 or not 1<=count<=1000
            or type(packets) is not list or len(packets)>MAX_EVENTS):
        raise ValueError('INVALID_WATERMARK_SCHEDULE')
    cursor=cursor_factory();times=[];last_received=-1
    for packet in packets:
        received=packet['received_at_ms']
        if type(received) is not int or received<last_received:
            raise WindowError('INVALID_RECEIVE_ORDER')
        last_received=received
        trade=cursor.apply(packet['payload'],received_at_ms=received)
        if trade is None:continue
        end=start+(len(times)+1)*width
        if trade.transaction_time_ms<end:continue
        decision=max(end,received)
        # The original window builder enforces predecessor, sequence, nonempty
        # coverage and age. No skipped window or later replacement is allowed.
        build_window(packets,start_ms=end-width,end_ms=end,as_of_ms=decision,
                     max_age_ms=age,cursor_factory=cursor_factory)
        if times and decision<=times[-1]:raise WindowError('NONINCREASING_WATERMARK_DECISION')
        times.append(decision)
        if len(times)==count:return times
    raise WindowError('MISSING_WINDOW_COVERAGE')
