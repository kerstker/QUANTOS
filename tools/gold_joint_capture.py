"""Stop a broken paired capture and drain its bounded public quote worker."""
import asyncio


async def collect_joint(stream, quotes, stopped):
    tasks=[asyncio.create_task(stream),asyncio.create_task(quotes)]
    pending=set(tasks)
    values=[None,None]
    try:
        while pending:
            done,pending=await asyncio.wait(pending,return_when=asyncio.FIRST_COMPLETED)
            failed=False
            for task in done:
                index=tasks.index(task)
                try:values[index]=task.result()
                except BaseException as exc:values[index]=exc
                failed |= isinstance(values[index],BaseException) or (
                    index==0 and isinstance(values[index],dict) and values[index].get('status')!='OBSERVED')
            if failed:
                stopped.set()
                # A to_thread coroutine must finish cooperatively; cancelling its
                # asyncio wrapper alone would leave the network writer running.
                if tasks[0] in pending:
                    tasks[0].cancel()
                remaining=await asyncio.gather(*tasks,return_exceptions=True)
                return remaining
        return values
    except BaseException:
        stopped.set()
        if not tasks[0].done():tasks[0].cancel()
        await asyncio.shield(asyncio.gather(*tasks,return_exceptions=True))
        raise
