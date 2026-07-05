"""
Database connection and table management for The Lab Manager.
"""

import aiomysql
import os
from typing import Optional, Any


# ◸──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◹
#       SECTION: Database Connection Class
# ◺──────── ✧ ──────── ◇ ———————🔹-💠-🔹——————— ◇ ──────── ✧ ────────◿

class Database:
    """Async MySQL database handler."""

    def __init__(self):
        self.pool: Optional[aiomysql.Pool] = None

    async def initialize(self) -> None:
        """Create connection pool and initialize tables."""
        self.pool = await aiomysql.create_pool(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            port=int(os.getenv('MYSQL_PORT', 3306)),
            user=os.getenv('MYSQL_USER'),
            password=os.getenv('MYSQL_PASSWORD'),
            db=os.getenv('MYSQL_DATABASE'),
            charset='utf8mb4',
            autocommit=True,
            minsize=1,
            maxsize=2,  # Reduced for shared hosted DB
            pool_recycle=1800
        )
        await self._create_tables()
        print("Database connected and tables initialized")

    async def _create_tables(self) -> None:
        """No-op for the Stardust Radio Bot.

        The cogs create the tables they use (link_tracker ->
        tracked_links / tracking_channels; radio_submit -> radio_submissions),
        and the shared MariaDB already has them from the Utility Bot.
        """
        return
    async def execute(self, query: str, params: tuple = ()) -> int:
        """Execute a query and return affected rows or last insert id."""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return cur.lastrowid if cur.lastrowid else cur.rowcount

    async def fetchone(self, query: str, params: tuple = ()) -> Optional[tuple]:
        """Fetch a single row."""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    async def fetchall(self, query: str, params: tuple = ()) -> list:
        """Fetch all rows."""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return await cur.fetchall()

    async def fetchone_dict(self, query: str, params: tuple = ()) -> Optional[dict]:
        """Fetch a single row as dictionary."""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    async def fetchall_dict(self, query: str, params: tuple = ()) -> list:
        """Fetch all rows as dictionaries."""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                return await cur.fetchall()

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
