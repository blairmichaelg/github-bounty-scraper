import asyncio

import aiosqlite

from github_bounty_scraper.db import init_db


async def main():
    async with aiosqlite.connect('bounty_stats.db') as conn:
        await init_db(conn)

if __name__ == "__main__":
    asyncio.run(main())
