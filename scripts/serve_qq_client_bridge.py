"""Serve the experimental native QQ driver without changing Miko configuration.

Load apps/qq-bridge/client.js into QQ first. The private JSON configuration
supplies account, port and token; the same token protects this local endpoint.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/backend/src'))
from miko.adapters.qq.bridge import ClientBridge
from miko.adapters.qq.client_driver import NativeClientDriver


async def serve(config_path, database, port):
    config = json.loads(config_path.read_text('utf-8'))
    driver = NativeClientDriver(config, database)
    bridge = ClientBridge(driver, str(config['account']), config['token'])
    server = uvicorn.Server(uvicorn.Config(bridge.app, host='127.0.0.1', port=port,
                                         log_level='warning'))
    tasks = []
    try:
        await bridge.identity()
        listener = asyncio.create_task(driver.listen(bridge.publish))
        running = asyncio.create_task(server.serve())
        tasks = [listener, running]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # Stop the endpoint if its native event stream fails. Do not continue
        # accepting sends while owner's interruption events cannot be observed.
        server.should_exit = True
        if listener in done:
            await listener
            raise ConnectionError('QQ event stream closed')
        await running
    finally:
        server.should_exit = True
        if tasks:
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            await tasks[1]
        await driver.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--port', type=int, default=17972)
    args = parser.parse_args()
    asyncio.run(serve(args.config, args.database, args.port))
