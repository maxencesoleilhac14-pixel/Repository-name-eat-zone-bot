from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
import httpx
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import basicfit
import cuisto


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int | None = None) -> int | None:
    value = env_str(name)
    if not value:
        return default
    return int(value)


def env_float(name: str, default: float = 0.0) -> float:
    value = env_str(name)
    if not value:
        return default
    return float(value.replace(",", "."))


def env_ids(name: str) -> tuple[int, ...]:
    raw = env_str(name)
    if not raw:
        return ()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.append(int(part))
    return tuple(dict.fromkeys(ids))


def env_lines(name: str, default: str = "") -> str:
    return os.getenv(name, default).replace("\\n", "\n").strip()


def parse_amount(raw: str) -> float:
    cleaned = raw.strip().replace("€", "").replace("eur", "").replace("EUR", "").replace(",", ".")
    return round(float(cleaned), 2)


def money(value: float) -> str:
    return f"{value:.2f} EUR"


def safe_channel_name(value: str, fallback: str = "ticket") -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9_-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return (value or fallback)[:80]


async def send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


@dataclass(slots=True)
class Settings:
    token: str
    guild_id: int | None
    brand_name: str
    verified_role_id: int | None
    welcome_channel_id: int | None
    ticket_category_id: int | None
    ticket_pending_category_id: int | None
    ticket_active_category_id: int | None
    paid_ticket_category_id: int | None
    support_ticket_category_id: int | None
    transcript_channel_id: int | None
    review_channel_id: int | None
    loyal_customer_role_id: int | None
    staff_role_id: int | None
    maitre_cuisto_role_id: int | None
    second_staff_role_id: int | None
    third_staff_role_id: int | None
    support_role_id: int | None
    admin_role_ids: tuple[int, ...]
    paypal_link: str
    paypal_text: str
    revolut_link: str
    revolut_text: str
    paysafe_link: str
    paysafe_text: str
    payment_confirmed_message: str
    oxapay_api_key: str
    oxapay_invoice_url: str
    oxapay_status_url: str
    oxapay_currency: str
    oxapay_allowed_coins: tuple[str, ...]
    oxapay_lifetime_minutes: int
    oxapay_status_poll_seconds: int
    oxapay_success_status: str
    crypto_success_message: str
    default_staff_percent_rate: float
    data_dir: Path
    database_path: Path
    transcripts_dir: Path
    account_category_id: int | None
    account_log_channel_id: int | None
    account_payment_log_id: int | None
    founder_role_ids: tuple[int, ...]

    @property
    def order_role_ids(self) -> tuple[int, ...]:
        blocked_support_ids = {1495941332177391676}
        if self.support_role_id:
            blocked_support_ids.add(self.support_role_id)
        role_ids = (
            1506012403672940566,
            self.maitre_cuisto_role_id,
            self.staff_role_id,
            self.second_staff_role_id,
            self.third_staff_role_id,
        )
        return tuple(dict.fromkeys(x for x in role_ids if x and x not in blocked_support_ids))


def load_settings() -> Settings:
    data_dir = Path(env_str("BOT_DATA_DIR", "data")).expanduser()
    database_path = Path(env_str("DATABASE_PATH", str(data_dir / "eatzone.db"))).expanduser()
    transcripts_dir = Path(env_str("TRANSCRIPTS_DIR", str(data_dir / "transcripts"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        token=env_str("DISCORD_TOKEN"),
        guild_id=env_int("GUILD_ID"),
        brand_name=env_str("BRAND_NAME", "Eat Zone -50%"),
        verified_role_id=env_int("VERIFIED_ROLE_ID"),
        welcome_channel_id=env_int("WELCOME_CHANNEL_ID"),
        ticket_category_id=env_int("TICKET_CATEGORY_ID", 1495945701438259382),
        ticket_pending_category_id=env_int("TICKET_PENDING_CATEGORY_ID", 1495945701438259382),
        ticket_active_category_id=env_int("TICKET_ACTIVE_CATEGORY_ID", 1495945919042945106),
        paid_ticket_category_id=env_int("PAID_TICKET_CATEGORY_ID"),
        support_ticket_category_id=env_int("SUPPORT_TICKET_CATEGORY_ID", 1498079981757268210),
        transcript_channel_id=env_int("TRANSCRIPT_CHANNEL_ID"),
        review_channel_id=env_int("REVIEW_CHANNEL_ID", 1496869947727675462),
        loyal_customer_role_id=env_int("LOYAL_CUSTOMER_ROLE_ID", 1497210152439386112),
        staff_role_id=env_int("STAFF_ROLE_ID"),
        maitre_cuisto_role_id=env_int("MAITRE_CUISTO_ROLE_ID", 1495934353132486736),
        second_staff_role_id=env_int("SECOND_STAFF_ROLE_ID", 1495941332185780315),
        third_staff_role_id=env_int("THIRD_STAFF_ROLE_ID", 1498129393581817856),
        support_role_id=env_int("SUPPORT_ROLE_ID", 1495941332177391676),
        admin_role_ids=env_ids("ADMIN_ROLE_IDS"),
        paypal_link=env_str("PAYPAL_LINK", "https://www.paypal.me/SkyOress"),
        paypal_text=env_lines(
            "PAYPAL_TEXT",
            "PayPal amis/proches uniquement.\nNe rien mettre en note sous risque de refus de paiement.",
        ),
        revolut_link=env_str("REVOLUT_LINK", "https://revolut.me/nadegealine"),
        revolut_text=env_lines(
            "REVOLUT_TEXT",
            "Revolut uniquement.\nNe rien mettre en note sous risque de refus de paiement.",
        ),
        paysafe_link=env_str("PAYSAFE_LINK", "https://www.paysafecard.com/"),
        paysafe_text=env_lines(
            "PAYSAFE_TEXT",
            "Paysafe disponible.\nEnvoie le code paysafe en MP apres paiement.",
        ),
        payment_confirmed_message=env_str(
            "PAYMENT_CONFIRMED_MESSAGE",
            "✅ Paiement reçu, tu vas recevoir ton suivi de livraison.",
        ),
        oxapay_api_key=env_str("OXAPAY_API_KEY") or env_str("OXAPAY_MERCHANT_API_KEY"),
        oxapay_invoice_url=env_str("OXAPAY_INVOICE_URL", "https://api.oxapay.com/v1/payment/invoice"),
        oxapay_status_url=env_str("OXAPAY_STATUS_URL", "https://api.oxapay.com/v1/payment/{track_id}"),
        oxapay_currency=env_str("OXAPAY_CURRENCY", "EUR").upper(),
        oxapay_allowed_coins=tuple(x.strip().upper() for x in env_str("OXAPAY_ALLOWED_COINS", "SOL,BTC,ETH").split(",") if x.strip()),
        oxapay_lifetime_minutes=int(env_str("OXAPAY_LIFETIME_MINUTES", "30")),
        oxapay_status_poll_seconds=int(env_str("OXAPAY_STATUS_POLL_SECONDS", "30")),
        oxapay_success_status=env_str("OXAPAY_SUCCESS_STATUS", "paid").lower(),
        crypto_success_message=env_str("CRYPTO_SUCCESS_MESSAGE", "✅ Paiement confirmé"),
        default_staff_percent_rate=env_float("DEFAULT_STAFF_PERCENT_RATE", 50.0),
        data_dir=data_dir,
        database_path=database_path,
        transcripts_dir=transcripts_dir,
        account_category_id=env_int("ACCOUNT_CATEGORY_ID"),
        account_log_channel_id=env_int("ACCOUNT_LOG_CHANNEL_ID"),
        account_payment_log_id=env_int("ACCOUNT_PAYMENT_LOG_ID"),
        founder_role_ids=env_ids("FOUNDER_ROLE_IDS"),
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def init(self) -> None:
        basicfit.init_basicfit_tables(self)
        cuisto.init_cuisto_tables(self)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER,
                creator_id INTEGER NOT NULL,
                creator_name TEXT NOT NULL,
                ticket_type TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_by INTEGER,
                claimed_name TEXT,
                address TEXT NOT NULL DEFAULT '',
                restaurant TEXT NOT NULL DEFAULT '',
                amount_ht REAL NOT NULL DEFAULT 0,
                amount_ttc REAL NOT NULL DEFAULT 0,
                payment_method TEXT NOT NULL DEFAULT '',
                payment_enabled INTEGER NOT NULL DEFAULT 1,
                order_cost REAL NOT NULL DEFAULT 0,
                resale_amount REAL NOT NULL DEFAULT 0,
                profit_amount REAL NOT NULL DEFAULT 0,
                salary_amount REAL NOT NULL DEFAULT 0,
                fee_amount REAL NOT NULL DEFAULT 0,
                transcript_path TEXT,
                payout_paid_at TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                provider TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                payment_url TEXT,
                external_id TEXT,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS influence (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                available INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                discount_percent REAL NOT NULL DEFAULT 0,
                discount_fixed REAL NOT NULL DEFAULT 0,
                max_uses INTEGER NOT NULL DEFAULT 0,
                used_count INTEGER NOT NULL DEFAULT 0,
                expires_at TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()
        try:
            self.conn.execute("ALTER TABLE tickets ADD COLUMN fee_amount REAL NOT NULL DEFAULT 0")
            self.conn.commit()
        except Exception:
            pass

    def create_ticket(
        self,
        *,
        guild_id: int,
        creator_id: int,
        creator_name: str,
        ticket_type: str,
        address: str = "",
        restaurant: str = "",
        amount_ht: float = 0,
        amount_ttc: float = 0,
        payment_method: str = "",
        payment_enabled: bool = True,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO tickets (
                guild_id, creator_id, creator_name, ticket_type, status, address, restaurant,
                amount_ht, amount_ttc, payment_method, payment_enabled, created_at
            ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                creator_id,
                creator_name,
                ticket_type,
                address,
                restaurant,
                amount_ht,
                amount_ttc,
                payment_method,
                1 if payment_enabled else 0,
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def attach_channel(self, ticket_id: int, channel_id: int) -> None:
        self.conn.execute("UPDATE tickets SET channel_id = ? WHERE id = ?", (channel_id, ticket_id))
        self.conn.commit()

    def ticket_by_channel(self, channel_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)).fetchone()

    def ticket_by_id(self, ticket_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()

    def find_ticket_for_channel(self, channel_id: int, channel_name: str) -> sqlite3.Row | None:
        ticket = self.ticket_by_channel(channel_id)
        if ticket:
            return ticket
        match = re.search(r"(?:commande|support|achat)-(\d+)", channel_name.lower())
        if not match:
            return None
        ticket = self.ticket_by_id(int(match.group(1)))
        if ticket:
            self.attach_channel(int(ticket["id"]), channel_id)
            return self.ticket_by_id(int(ticket["id"]))
        return None

    def claim_ticket(self, ticket_id: int, user_id: int, user_name: str) -> None:
        self.conn.execute(
            "UPDATE tickets SET status = 'claimed', claimed_by = ?, claimed_name = ? WHERE id = ?",
            (user_id, user_name, ticket_id),
        )
        self.conn.commit()

    def unclaim_ticket(self, ticket_id: int) -> None:
        self.conn.execute(
            "UPDATE tickets SET status = 'open', claimed_by = NULL, claimed_name = NULL WHERE id = ?",
            (ticket_id,),
        )
        self.conn.commit()

    def close_ticket(
        self,
        ticket_id: int,
        *,
        transcript_path: str | None,
        order_cost: float,
        resale_amount: float,
        profit_amount: float,
        salary_amount: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE tickets
            SET status = 'closed', transcript_path = ?, order_cost = ?, resale_amount = ?,
                profit_amount = ?, salary_amount = ?, closed_at = ?
            WHERE id = ?
            """,
            (transcript_path, order_cost, resale_amount, profit_amount, salary_amount, now_iso(), ticket_id),
        )
        self.conn.commit()

    def create_payment(
        self,
        *,
        ticket_id: int,
        channel_id: int,
        kind: str,
        provider: str,
        amount: float,
        currency: str,
        status: str,
        payment_url: str | None,
        external_id: str | None,
        created_by: int,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO payments (
                ticket_id, channel_id, kind, provider, amount, currency, status,
                payment_url, external_id, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                channel_id,
                kind,
                provider,
                amount,
                currency,
                status,
                payment_url,
                external_id,
                created_by,
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_payment(self, payment_id: int, status: str) -> None:
        self.conn.execute("UPDATE payments SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), payment_id))
        self.conn.commit()

    def pending_crypto_payments(self) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM payments WHERE provider = 'oxapay' AND status = 'pending' ORDER BY id ASC"
        ).fetchall()
        return list(rows)

    def list_accounts(self, available_only: bool = True) -> list[sqlite3.Row]:
        if available_only:
            return list(self.conn.execute("SELECT * FROM accounts WHERE available = 1 ORDER BY name ASC").fetchall())
        return list(self.conn.execute("SELECT * FROM accounts ORDER BY name ASC").fetchall())

    def get_account(self, account_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    def add_account(self, *, name: str, price: float, description: str, expires_at: str | None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO accounts (name, price, description, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, price, description, expires_at, now_iso()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_account(self, account_id: int, **kwargs: Any) -> None:
        fields = {k: v for k, v in kwargs.items() if v is not None}
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [account_id]
        self.conn.execute(f"UPDATE accounts SET {set_clause} WHERE id = ?", values)
        self.conn.commit()

    def delete_account(self, account_id: int) -> None:
        self.conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.conn.commit()

    def list_promo_codes(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM promo_codes ORDER BY code ASC").fetchall())

    def get_promo_code(self, code: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM promo_codes WHERE code = ?", (code.strip().upper(),)).fetchone()

    def add_promo_code(self, *, code: str, discount_percent: float, discount_fixed: float, max_uses: int, expires_at: str | None) -> int:
        cursor = self.conn.execute(
            "INSERT INTO promo_codes (code, discount_percent, discount_fixed, max_uses, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (code.strip().upper(), discount_percent, discount_fixed, max_uses, expires_at, now_iso()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def delete_promo_code(self, promo_id: int) -> None:
        self.conn.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
        self.conn.commit()

    def increment_promo_uses(self, promo_id: int) -> None:
        self.conn.execute("UPDATE promo_codes SET used_count = used_count + 1 WHERE id = ?", (promo_id,))
        self.conn.commit()

    def update_promo_code(self, promo_id: int, **kwargs: Any) -> None:
        fields = {k: v for k, v in kwargs.items() if v is not None}
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [promo_id]
        self.conn.execute(f"UPDATE promo_codes SET {set_clause} WHERE id = ?", values)
        self.conn.commit()

    def set_influence(self, guild_id: int, channel_id: int, status: str) -> None:
        self.conn.execute(
            """
            INSERT INTO influence (guild_id, channel_id, status, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (guild_id, channel_id, status, now_iso()),
        )
        self.conn.commit()


class EatZoneBot(commands.Bot):
    def __init__(self, settings: Settings, db: Database) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.db = db

    async def setup_hook(self) -> None:
        self.db.init()
        self.add_view(CommandPanelView())
        self.add_view(SupportPanelView())
        self.add_view(TicketControlsView())
        self.add_view(InfluenceView())
        self.add_view(AccountPanelView())
        self.add_view(AdminPanelView())
        self.add_view(basicfit.BasicFitPanelView())
        self.add_view(basicfit.BasicFitTicketView())
        self.add_view(basicfit.BasicFitSendAccountView())
        self.add_view(basicfit.BasicFitAdminView())
        self.add_view(cuisto.CuistoPanelView())
        self.add_view(cuisto.CuistoAdminView())
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.crypto_poller.change_interval(seconds=max(10, self.settings.oxapay_status_poll_seconds))
        self.crypto_poller.start()
        self.cuisto_expiry_checker.start()

    async def close(self) -> None:
        self.crypto_poller.cancel()
        self.cuisto_expiry_checker.cancel()
        await super().close()

    @tasks.loop(seconds=30)
    async def crypto_poller(self) -> None:
        if not self.settings.oxapay_api_key:
            return
        for payment in self.db.pending_crypto_payments():
            external_id = payment["external_id"]
            if not external_id:
                continue
            status = await fetch_oxapay_status(self.settings, external_id)
            if status and status.lower() == self.settings.oxapay_success_status:
                self.db.update_payment(payment["id"], "paid")
                channel = self.get_channel(int(payment["channel_id"]))
                if isinstance(channel, discord.TextChannel):
                    await channel.send(self.settings.crypto_success_message)

    @tasks.loop(hours=1)
    async def cuisto_expiry_checker(self) -> None:
        if not self.settings.guild_id:
            return
        guild = self.get_guild(self.settings.guild_id)
        if not guild:
            return
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        expired = self.db.conn.execute(
            "SELECT user_id, current_week FROM cuisto_subscriptions WHERE active = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso,),
        ).fetchall()
        for row in expired:
            user_id = int(row["user_id"])
            member = guild.get_member(user_id)
            if not member:
                self.db.conn.execute(
                    "UPDATE cuisto_subscriptions SET active = 0, current_week = 0 WHERE user_id = ?",
                    (user_id,),
                )
                self.db.conn.commit()
                continue
            cuisto_role = guild.get_role(cuisto.CUISTO_ROLE_ID)
            apprenti_role = guild.get_role(cuisto.APPRENTI_ROLE_ID)
            maitre_role = guild.get_role(self.settings.maitre_cuisto_role_id) if self.settings.maitre_cuisto_role_id else None
            removed = []
            if cuisto_role and cuisto_role in member.roles:
                try:
                    await member.remove_roles(cuisto_role, reason="Abonnement cuisto expire")
                    removed.append("Cuisto")
                except discord.DiscordException:
                    pass
            if apprenti_role and apprenti_role in member.roles:
                try:
                    await member.remove_roles(apprenti_role, reason="Abonnement cuisto expire")
                    removed.append("Apprenti")
                except discord.DiscordException:
                    pass
            if maitre_role and maitre_role in member.roles:
                try:
                    await member.remove_roles(maitre_role, reason="Abonnement cuisto expire")
                    removed.append("Maitre")
                except discord.DiscordException:
                    pass
            grace_until = (now + timedelta(days=1)).isoformat()
            self.db.conn.execute(
                "UPDATE cuisto_subscriptions SET active = 2, grace_until = ? WHERE user_id = ?",
                (grace_until, user_id),
            )
            self.db.conn.commit()
            if removed:
                try:
                    await member.send(
                        f"\u274c Ton abonnement cuisto a expire le {now.strftime('%d/%m/%Y')}.\n"
                        f"Tes roles ont ete retires : **{', '.join(removed)}**.\n\n"
                        f"\u23f3 Tu as **1 jour** pour renouveler en repayant via le panel.\n"
                        f"Si tu renouvelles a temps, ta progression (semaine {row['current_week']}/{cuisto.WEEKS_TO_MASTER}) est conservee !\n"
                        f"Sinon, le compteur reviendra a zero."
                    )
                except discord.DiscordException:
                    pass

        grace_expired = self.db.conn.execute(
            "SELECT user_id FROM cuisto_subscriptions WHERE active = 2 AND grace_until IS NOT NULL AND grace_until <= ?",
            (now_iso,),
        ).fetchall()
        for row in grace_expired:
            user_id = int(row["user_id"])
            member = guild.get_member(user_id)
            self.db.conn.execute(
                "UPDATE cuisto_subscriptions SET active = 0, current_week = 0 WHERE user_id = ?",
                (user_id,),
            )
            self.db.conn.commit()
            if member:
                try:
                    await member.send(
                        f"\u274c\u274c Ton delai de grace de 1 jour est termine.\n"
                        f"Ton compteur cuisto a ete remis a **zero**.\n"
                        f"Tu peux recommencer via le panel quand tu veux !"
                    )
                except discord.DiscordException:
                    pass


def member_has_role(member: discord.Member, role_ids: tuple[int, ...]) -> bool:
    current = {role.id for role in member.roles}
    return any(role_id in current for role_id in role_ids)


def is_admin(bot: EatZoneBot, member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member_has_role(member, bot.settings.admin_role_ids)


def can_handle_orders(bot: EatZoneBot, member: discord.Member) -> bool:
    return is_admin(bot, member) or member_has_role(member, bot.settings.order_role_ids)


def can_handle_support(bot: EatZoneBot, member: discord.Member) -> bool:
    support_ids = tuple(x for x in (bot.settings.support_role_id, *bot.settings.order_role_ids) if x)
    return is_admin(bot, member) or member_has_role(member, support_ids)


def ticket_mentions(bot: EatZoneBot, ticket_type: str) -> str:
    if ticket_type == "support" and bot.settings.support_role_id:
        return f"<@&{bot.settings.support_role_id}>"
    return "<@&1506012403672940566>"


async def category_from_id(guild: discord.Guild, category_id: int | None) -> discord.CategoryChannel | None:
    if not category_id:
        return None
    channel = guild.get_channel(category_id)
    return channel if isinstance(channel, discord.CategoryChannel) else None


async def is_influence_off(bot: EatZoneBot, guild: discord.Guild) -> bool:
    record = bot.db.conn.execute(
        "SELECT status FROM influence WHERE guild_id = ?", (guild.id,)
    ).fetchone()
    return record is not None and record["status"] == "OFF"


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = value.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    return re.sub(r"[^a-z0-9]+", "", value)


async def find_or_create_category(
    guild: discord.Guild,
    *,
    configured_id: int | None,
    candidates: tuple[str, ...],
    create_name: str,
) -> discord.CategoryChannel:
    configured = await category_from_id(guild, configured_id)
    if configured:
        return configured

    normalized_candidates = {normalize_name(candidate) for candidate in candidates}
    for category in guild.categories:
        normalized = normalize_name(category.name)
        if normalized in normalized_candidates:
            return category
        if any(candidate in normalized for candidate in normalized_candidates):
            return category

    return await guild.create_category(create_name, reason="Categorie tickets automatique")


async def make_ticket_overwrites(
    bot: EatZoneBot,
    guild: discord.Guild,
    creator: discord.Member,
    ticket_type: str,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        creator: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    role_ids = (bot.settings.support_role_id,) if ticket_type == "support" else bot.settings.order_role_ids
    for role_id in role_ids:
        if not role_id:
            continue
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for role_id in bot.settings.admin_role_ids:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
    return overwrites


async def create_order_ticket(
    bot: EatZoneBot,
    interaction: discord.Interaction,
    *,
    address: str,
    restaurant: str,
    amount_ht: float,
    amount_ttc: float,
    payment_method: str,
    fee_amount: float = 0,
) -> None:
    assert interaction.guild and isinstance(interaction.user, discord.Member)
    if await is_influence_off(bot, interaction.guild):
        await send_ephemeral(interaction, "❌ Les commandes sont désactivées (statut OFF).")
        return
    ticket_id = bot.db.create_ticket(
        guild_id=interaction.guild.id,
        creator_id=interaction.user.id,
        creator_name=str(interaction.user),
        ticket_type="order",
        address=address,
        restaurant=restaurant,
        amount_ht=amount_ht,
        amount_ttc=amount_ttc,
        payment_method=payment_method,
    )
    if fee_amount > 0:
        bot.db.conn.execute("UPDATE tickets SET fee_amount = ? WHERE id = ?", (fee_amount, ticket_id))
        bot.db.conn.commit()
    category = await find_or_create_category(
        interaction.guild,
        configured_id=bot.settings.ticket_pending_category_id or bot.settings.ticket_category_id,
        candidates=("commande non traiter", "commande non traitee", "ticket non traiter", "ticket non traitee"),
        create_name="Commande non traiter",
    )
    overwrites = await make_ticket_overwrites(bot, interaction.guild, interaction.user, "order")
    channel = await interaction.guild.create_text_channel(
        name=f"commande-{ticket_id:04d}",
        category=category,
        overwrites=overwrites,
        reason=f"Ticket commande #{ticket_id}",
    )
    bot.db.attach_channel(ticket_id, channel.id)
    embed = discord.Embed(title=f"{bot.settings.brand_name} | Commande #{ticket_id}", color=0x2ECC71)
    embed.add_field(name="Client", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
    embed.add_field(name="Adresse & ville", value=f"```{address}```", inline=False)
    embed.add_field(name="Restaurant", value=f"```{restaurant}```", inline=False)
    embed.add_field(name="Montant HT", value=f"`{money(amount_ht)}`", inline=True)
    embed.add_field(name="Montant TTC", value=f"`{money(amount_ttc)}`", inline=True)
    embed.add_field(name="Paiement", value=f"```{payment_method}```", inline=True)
    if fee_amount > 0:
        embed.add_field(name="Frais", value=f"`{money(fee_amount)}`", inline=True)
    embed.add_field(name="Statut", value="`En attente`", inline=False)
    await channel.send(ticket_mentions(bot, "order"), embed=embed, view=TicketControlsView())
    await channel.send(f"{interaction.user.mention} ton ticket est ouvert ici. Un cuisto peut le claim quand il est prêt.")
    await send_ephemeral(interaction, f"✅ Ticket créé : {channel.mention}")


async def create_support_ticket(bot: EatZoneBot, interaction: discord.Interaction, reason: str) -> None:
    assert interaction.guild and isinstance(interaction.user, discord.Member)
    ticket_id = bot.db.create_ticket(
        guild_id=interaction.guild.id,
        creator_id=interaction.user.id,
        creator_name=str(interaction.user),
        ticket_type="support",
        address=reason,
        payment_enabled=False,
    )
    category = await find_or_create_category(
        interaction.guild,
        configured_id=bot.settings.support_ticket_category_id,
        candidates=("ticket support", "support", "tickets support"),
        create_name="Ticket Support",
    )
    overwrites = await make_ticket_overwrites(bot, interaction.guild, interaction.user, "support")
    channel = await interaction.guild.create_text_channel(
        name=f"support-{ticket_id:04d}",
        category=category,
        overwrites=overwrites,
        reason=f"Ticket support #{ticket_id}",
    )
    bot.db.attach_channel(ticket_id, channel.id)
    embed = discord.Embed(
        title=f"{bot.settings.brand_name} | Support #{ticket_id}",
        description="Le staff te répondra dès que possible.",
        color=0x3498DB,
    )
    embed.add_field(name="Client", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
    embed.add_field(name="Raison", value=f"```{reason}```", inline=False)
    await channel.send(ticket_mentions(bot, "support"), embed=embed, view=SupportTicketCloseView())
    await send_ephemeral(interaction, f"✅ Ticket support créé : {channel.mention}")


async def finalize_claim_ticket(
    bot: EatZoneBot,
    channel: discord.TextChannel,
    guild: discord.Guild | None,
    staff_display_name: str,
    staff_mention: str,
) -> None:
    try:
        await asyncio.wait_for(channel.send(f"✅ Ticket pris en charge par {staff_mention}."), timeout=3)
    except (discord.DiscordException, asyncio.TimeoutError):
        pass

    category = None
    if guild:
        active_channel = guild.get_channel(bot.settings.ticket_active_category_id or 0)
        category = active_channel if isinstance(active_channel, discord.CategoryChannel) else None

    try:
        await asyncio.wait_for(
            channel.edit(
                name=safe_channel_name(f"commande-{staff_display_name}"),
                category=category or channel.category,
                reason="Ticket claim",
            ),
            timeout=3,
        )
    except (discord.DiscordException, asyncio.TimeoutError):
        pass


async def finalize_unclaim_ticket(
    bot: EatZoneBot,
    channel: discord.TextChannel,
    guild: discord.Guild | None,
    ticket_id: int,
) -> None:
    try:
        await asyncio.wait_for(channel.send("✅ Ticket remis en attente. Il peut être repris maintenant."), timeout=3)
    except (discord.DiscordException, asyncio.TimeoutError):
        pass

    category = None
    if guild:
        pending_channel = guild.get_channel(bot.settings.ticket_pending_category_id or bot.settings.ticket_category_id or 0)
        category = pending_channel if isinstance(pending_channel, discord.CategoryChannel) else None

    try:
        await asyncio.wait_for(
            channel.edit(
                name=f"commande-{ticket_id:04d}",
                category=category or channel.category,
                reason="Ticket unclaim",
            ),
            timeout=3,
        )
    except (discord.DiscordException, asyncio.TimeoutError):
        pass


async def move_ticket_to_paid_category(
    bot: EatZoneBot,
    channel: discord.TextChannel,
    guild: discord.Guild | None,
    ticket_id: int,
) -> None:
    if guild is None:
        return

    try:
        category = await find_or_create_category(
            guild,
            configured_id=bot.settings.paid_ticket_category_id,
            candidates=(
                "commandes payees",
                "commande payee",
                "tickets payes",
                "ticket paye",
                "paiements confirmes",
            ),
            create_name="Commandes payees",
        )
    except discord.DiscordException:
        return

    try:
        await asyncio.wait_for(
            channel.edit(
                name=f"payee-{ticket_id:04d}",
                category=category,
                reason="Paiement confirme",
            ),
            timeout=3,
        )
    except (discord.DiscordException, asyncio.TimeoutError):
        pass


class OrderModal(discord.ui.Modal, title="Formulaire de Commande"):
    address = discord.ui.TextInput(label="Adresse & Ville", placeholder="Ex: 12 rue de la Paix, Paris...", max_length=180)
    restaurant = discord.ui.TextInput(label="Nom du restaurant", placeholder="Ex: McDonald's...", max_length=100)
    amount_ht = discord.ui.TextInput(label="Montant du panier HT", placeholder="Ex: 25.50", max_length=20)
    amount_ttc = discord.ui.TextInput(label="Montant du panier TTC", placeholder="Ex: 30.60", max_length=20)
    payment = discord.ui.TextInput(label="Moyen de paiement", placeholder="PayPal, Revolut, crypto...", max_length=80)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        try:
            ht = parse_amount(str(self.amount_ht))
            ttc = parse_amount(str(self.amount_ttc))
        except ValueError:
            await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await create_order_ticket(
            bot,
            interaction,
            address=str(self.address),
            restaurant=str(self.restaurant),
            amount_ht=ht,
            amount_ttc=ttc,
            payment_method=str(self.payment),
        )


class SupportModal(discord.ui.Modal, title="Ticket Support"):
    reason = discord.ui.TextInput(
        label="Raison du ticket",
        placeholder="Explique rapidement ton probleme...",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True, thinking=True)
        await create_support_ticket(bot, interaction, str(self.reason))


class CommandPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Commander", style=discord.ButtonStyle.success, emoji="🍔", custom_id="ez:panel:order")
    async def order(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if interaction.guild and await is_influence_off(bot, interaction.guild):
            await interaction.response.send_message("❌ Les commandes sont désactivées (statut OFF). Seul le support est disponible.", ephemeral=True)
            return
        await interaction.response.send_modal(OrderModal())


class SupportPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Support", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ez:panel:support")
    async def support(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(SupportModal())


class SupportTicketCloseView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ez:support:close")
    async def close_support(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
            await interaction.response.send_message("❌ Seul le staff peut fermer ce ticket.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await close_current_ticket(bot, interaction, order_cost=0, resale_amount=0, blank=True)


class AccountPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Voir les comptes", style=discord.ButtonStyle.success, emoji="📦", custom_id="ez:account:view")
    async def view_accounts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        accounts = bot.db.list_accounts(available_only=True)
        if not accounts:
            await interaction.response.send_message("❌ Aucun compte disponible pour le moment.", ephemeral=True)
            return
        options = [
            discord.SelectOption(label=f"{a['name']} - {a['price']:.2f} EUR", value=str(a['id']), description=a['description'][:50] or "Pas de description")
            for a in accounts
        ]
        view = AccountSelectView(options)
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Comptes disponibles",
            description="Sélectionne un compte ci-dessous pour l'acheter.\n\nLes ventes sont traitées manuellement par notre équipe. Les produits peuvent ne pas être disponibles immédiatement.",
            color=0x9B59B6,
        )
        embed.add_field(
            name="⚠️ Garantie",
            value=(
                "La garantie des comptes nécessite obligatoirement la **tech Ub*r**.\n\n"
                "**Aucun remplacement ne sera proposé** si le compte flag, "
                "ou si le contenu promis n'est pas sur le compte (avec preuve).\n"
                "Les comptes sont vérifiés à l'avance."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class AccountSelectView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.add_item(AccountSelectMenu(options))


class AccountSelectMenu(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Choisis un compte...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        account_id = int(self.values[0])
        account = bot.db.get_account(account_id)
        if not account or not account["available"]:
            await interaction.response.send_message("❌ Ce compte n'est plus disponible.", ephemeral=True)
            return
        if interaction.guild and await is_influence_off(bot, interaction.guild):
            await interaction.response.send_message("❌ Les achats de comptes sont désactivés (statut OFF). Seul le support est disponible.", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        category = await category_from_id(interaction.guild, bot.settings.account_category_id)
        if not category:
            await interaction.response.send_message("❌ Catégorie des tickets comptes non configurée.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ticket_id = bot.db.create_ticket(
            guild_id=interaction.guild.id,
            creator_id=interaction.user.id,
            creator_name=str(interaction.user),
            ticket_type="account",
            address=f"Compte #{account_id}",
            payment_enabled=True,
        )
        account_role = interaction.guild.get_role(1514068068320411658)
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
        }
        if account_role:
            overwrites[account_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        for role_id in bot.settings.founder_role_ids:
            role = interaction.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await interaction.guild.create_text_channel(
            name=f"achat-{ticket_id:04d}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket achat compte #{ticket_id}",
        )
        bot.db.attach_channel(ticket_id, channel.id)
        embed = discord.Embed(title=f"{bot.settings.brand_name} | Achat Compte #{ticket_id}", color=0x9B59B6)
        embed.add_field(name="Client", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        embed.add_field(name="Compte", value=f"**{account['name']}**", inline=False)
        embed.add_field(name="Prix", value=f"**{account['price']:.2f} EUR**", inline=True)
        if account["description"]:
            embed.add_field(name="Description", value=account["description"], inline=False)
        if account["expires_at"]:
            embed.add_field(name="Expire le", value=account["expires_at"], inline=True)
        embed.set_footer(text="Les ventes sont traitées manuellement par notre équipe.")
        info = (
            ">>> ⚠️ **Fonctionnement des achats de comptes :**\n\n"
            "Les ventes sont traitées **manuellement** par notre équipe. "
            "Les produits peuvent ne pas être disponibles immédiatement.\n\n"
            "**📱 Commande :**\n"
            "• La demande de votre code/accès est disponible dans les **5 minutes** qui suivent l'achat.\n"
            "• Sinon, disponible aussi en **ticket support** (mais on ne garantit pas la réponse immédiate).\n\n"
            "**💳 Paiement :**\n"
            "• Un staff s'occupe de la transaction.\n"
            "• Après paiement, envoie une **preuve de paiement** (capture d'écran) dans ce ticket."
        )
        await channel.send(info)
        mention = f"<@&1514068068320411658>" if account_role else "@staff"
        await channel.send(mention)
        await channel.send(embed=embed, view=AccountTicketView(account_id=account_id, base_price=account["price"], ticket_db_id=ticket_id))
        await interaction.followup.send(f"✅ Ticket créé : {channel.mention}", ephemeral=True)
        log_channel = bot.get_channel(bot.settings.account_log_channel_id) if bot.settings.account_log_channel_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            log_embed = discord.Embed(title="📦 Nouvel achat compte", color=0x9B59B6, timestamp=datetime.now(timezone.utc))
            log_embed.add_field(name="Client", value=f"{interaction.user.mention} (`{interaction.user.id}`)")
            log_embed.add_field(name="Compte", value=account["name"])
            log_embed.add_field(name="Ticket", value=channel.mention)
            await log_channel.send(embed=log_embed)


class AccountTicketView(discord.ui.View):
    def __init__(self, account_id: int, base_price: float, ticket_db_id: int) -> None:
        super().__init__(timeout=None)
        self.account_id = account_id
        self.base_price = base_price
        self.current_price = base_price
        self.ticket_db_id = ticket_db_id
        self.promo_code: str | None = None

    @discord.ui.button(label="Code promo", style=discord.ButtonStyle.secondary, emoji="🎫", custom_id="ez:account:promo")
    async def promo(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(PromoModal(self))

    @discord.ui.button(label="PayPal", style=discord.ButtonStyle.secondary, emoji="💸", custom_id="ez:account:paypal")
    async def paypal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        embed = discord.Embed(title="Paiement PayPal - Achat Compte", color=0x1ABC9C)
        lines = [f"Produit: **Compte #{self.account_id}**"]
        if self.promo_code:
            lines.append(f"Code promo: **{self.promo_code}**")
        lines.append(f"Prix final: **{self.current_price:.2f} EUR**")
        lines.append(f"\n{bot.settings.paypal_text}")
        lines.append(f"\nLien : https://www.paypal.me/SkyOress")
        lines.append(f"\n📸 Après paiement, envoie une capture d'écran/vidéo comme preuve de paiement dans ce ticket.")
        embed.description = "\n".join(lines)
        bot.db.create_payment(
            ticket_id=self.ticket_db_id,
            channel_id=interaction.channel.id,
            kind="paypal",
            provider="manual",
            amount=self.current_price,
            currency="EUR",
            status="pending",
            payment_url="https://www.paypal.me/SkyOress",
            external_id=None,
            created_by=interaction.user.id,
        )
        await interaction.response.send_message("✅ PayPal envoyé. Envoie une preuve de paiement après avoir payé.", ephemeral=True)
        await interaction.channel.send(embed=embed)
        log_channel = bot.get_channel(bot.settings.account_payment_log_id) if bot.settings.account_payment_log_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"💳 Paiement PayPal pour compte #{self.account_id} - {self.current_price:.2f} EUR - Ticket <#{interaction.channel.id}>")

    @discord.ui.button(label="Revolut", style=discord.ButtonStyle.secondary, emoji="💳", custom_id="ez:account:revolut")
    async def revolut(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        embed = discord.Embed(title="Paiement Revolut - Achat Compte", color=0x9B59B6)
        lines = [f"Produit: **Compte #{self.account_id}**"]
        if self.promo_code:
            lines.append(f"Code promo: **{self.promo_code}**")
        lines.append(f"Prix final: **{self.current_price:.2f} EUR**")
        lines.append(f"\n{bot.settings.revolut_text}")
        lines.append(f"\nLien : https://revolut.me/nadegealine")
        lines.append(f"\n📸 Après paiement, envoie une capture d'écran/vidéo comme preuve de paiement dans ce ticket.")
        embed.description = "\n".join(lines)
        bot.db.create_payment(
            ticket_id=self.ticket_db_id,
            channel_id=interaction.channel.id,
            kind="revolut",
            provider="manual",
            amount=self.current_price,
            currency="EUR",
            status="pending",
            payment_url="https://revolut.me/nadegealine",
            external_id=None,
            created_by=interaction.user.id,
        )
        await interaction.response.send_message("✅ Revolut envoyé. Envoie une preuve de paiement après avoir payé.", ephemeral=True)
        await interaction.channel.send(embed=embed)
        log_channel = bot.get_channel(bot.settings.account_payment_log_id) if bot.settings.account_payment_log_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"💳 Paiement Revolut pour compte #{self.account_id} - {self.current_price:.2f} EUR - Ticket <#{interaction.channel.id}>")

    @discord.ui.button(label="Crypto", style=discord.ButtonStyle.secondary, emoji="🪙", custom_id="ez:account:crypto")
    async def crypto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        try:
            payment_url, external_id = await create_oxapay_invoice(bot.settings, self.current_price, self.ticket_db_id)
        except RuntimeError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        bot.db.create_payment(
            ticket_id=self.ticket_db_id,
            channel_id=interaction.channel.id,
            kind="crypto",
            provider="oxapay",
            amount=self.current_price,
            currency=bot.settings.oxapay_currency,
            status="pending",
            payment_url=payment_url,
            external_id=external_id,
            created_by=interaction.user.id,
        )
        embed = discord.Embed(title="Paiement Crypto - Achat Compte", color=0xF39C12)
        lines = [f"Produit: **Compte #{self.account_id}**"]
        if self.promo_code:
            lines.append(f"Code promo: **{self.promo_code}**")
        lines.append(f"Prix final: **{self.current_price:.2f} EUR**")
        lines.append(f"\n[Cliquer ici pour payer]({payment_url})")
        embed.description = "\n".join(lines)
        await interaction.response.send_message("✅ Lien crypto envoyé.", ephemeral=True)
        await interaction.channel.send(embed=embed)
        log_channel = bot.get_channel(bot.settings.account_payment_log_id) if bot.settings.account_payment_log_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"🪙 Paiement Crypto pour compte #{self.account_id} - {self.current_price:.2f} EUR - Ticket <#{interaction.channel.id}>")

    @discord.ui.button(label="Paysafe", style=discord.ButtonStyle.secondary, emoji="💴", custom_id="ez:account:paysafe")
    async def paysafe(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        embed = discord.Embed(title="Paiement Paysafe - Achat Compte", color=0x3498DB)
        lines = [f"Produit: **Compte #{self.account_id}**"]
        if self.promo_code:
            lines.append(f"Code promo: **{self.promo_code}**")
        lines.append(f"Prix final: **{self.current_price:.2f} EUR**")
        lines.append(f"\n{bot.settings.paysafe_text}")
        lines.append(f"\nLien : {bot.settings.paysafe_link}")
        lines.append(f"\n📸 Après paiement, envoie une capture d'écran comme preuve de paiement dans ce ticket.")
        embed.description = "\n".join(lines)
        bot.db.create_payment(
            ticket_id=self.ticket_db_id,
            channel_id=interaction.channel.id,
            kind="paysafe",
            provider="manual",
            amount=self.current_price,
            currency="EUR",
            status="pending",
            payment_url=bot.settings.paysafe_link,
            external_id=None,
            created_by=interaction.user.id,
        )
        await interaction.response.send_message("✅ Paysafe envoyé. Envoie une preuve de paiement après avoir payé.", ephemeral=True)
        await interaction.channel.send(embed=embed)
        log_channel = bot.get_channel(bot.settings.account_payment_log_id) if bot.settings.account_payment_log_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"💴 Paiement Paysafe pour compte #{self.account_id} - {self.current_price:.2f} EUR - Ticket <#{interaction.channel.id}>")

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ez:account:close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        if not isinstance(interaction.user, discord.Member) or not (is_founder(bot, interaction.user) or interaction.user.get_role(1514068068320411658)):
            await interaction.response.send_message("❌ Seul le staff compte peut fermer.", ephemeral=True)
            return
        await close_current_ticket(bot, interaction, order_cost=0, resale_amount=0, blank=True)


class PromoModal(discord.ui.Modal, title="Code promo"):
    code_input = discord.ui.TextInput(label="Code promo", placeholder="Ex: BIENVENUE10", max_length=30)

    def __init__(self, parent_view: AccountTicketView) -> None:
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        code_str = str(self.code_input).strip().upper()
        promo = bot.db.get_promo_code(code_str)
        if not promo:
            await interaction.response.send_message("❌ Code promo invalide.", ephemeral=True)
            return
        now = datetime.now(timezone.utc)
        if promo["expires_at"]:
            try:
                exp = datetime.fromisoformat(promo["expires_at"])
                if now > exp:
                    await interaction.response.send_message("❌ Ce code promo a expiré.", ephemeral=True)
                    return
            except ValueError:
                pass
        if promo["max_uses"] > 0 and promo["used_count"] >= promo["max_uses"]:
            await interaction.response.send_message("❌ Ce code promo a atteint sa limite d'utilisations.", ephemeral=True)
            return
        discount = 0.0
        if promo["discount_fixed"] > 0:
            discount = promo["discount_fixed"]
        elif promo["discount_percent"] > 0:
            discount = self.parent_view.base_price * promo["discount_percent"] / 100
        new_price = max(0, self.parent_view.base_price - discount)
        self.parent_view.current_price = new_price
        self.parent_view.promo_code = promo["code"]
        bot.db.increment_promo_uses(promo["id"])
        embed = discord.Embed(title="🎫 Code promo appliqué", color=0x2ECC71)
        embed.description = (
            f"Prix initial: **{self.parent_view.base_price:.2f} EUR**\n"
            f"Réduction: **{discount:.2f} EUR**\n"
            f"Prix final: **{new_price:.2f} EUR**"
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)
        log_channel = bot.get_channel(bot.settings.account_log_channel_id) if bot.settings.account_log_channel_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            await log_channel.send(f"🎫 Code promo `{promo['code']}` utilisé sur compte #{self.parent_view.account_id} (-{discount:.2f} EUR) - Ticket <#{interaction.channel.id}>")


class AdminAccountModal(discord.ui.Modal):
    def __init__(self, account: sqlite3.Row | None = None) -> None:
        title = "Modifier le compte" if account else "Ajouter un compte"
        super().__init__(title=title)
        self.account = account
        self.name_input = discord.ui.TextInput(label="Nom du compte", max_length=100, default=account["name"] if account else "")
        self.price_input = discord.ui.TextInput(label="Prix (EUR)", max_length=20, default=f"{account['price']:.2f}" if account else "")
        self.desc_input = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=500, default=account["description"] if account else "", required=False)
        self.expires_input = discord.ui.TextInput(label="Date expiration (ISO ou vide)", max_length=30, default=account["expires_at"] or "" if account else "", required=False)
        self.add_item(self.name_input)
        self.add_item(self.price_input)
        self.add_item(self.desc_input)
        self.add_item(self.expires_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        try:
            price = float(str(self.price_input).replace(",", "."))
        except ValueError:
            await interaction.response.send_message("❌ Prix invalide.", ephemeral=True)
            return
        name = str(self.name_input).strip()
        if not name:
            await interaction.response.send_message("❌ Nom requis.", ephemeral=True)
            return
        desc = str(self.desc_input).strip()
        expires = str(self.expires_input).strip() or None
        if self.account:
            bot.db.update_account(self.account["id"], name=name, price=price, description=desc, expires_at=expires)
            await interaction.response.send_message(f"✅ Compte **{name}** modifié.", ephemeral=True)
        else:
            bot.db.add_account(name=name, price=price, description=desc, expires_at=expires)
            await interaction.response.send_message(f"✅ Compte **{name}** ajouté.", ephemeral=True)
        log_channel = bot.get_channel(bot.settings.account_log_channel_id) if bot.settings.account_log_channel_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            action = "modifié" if self.account else "ajouté"
            await log_channel.send(f"📝 Compte **{name}** {action} par {interaction.user.mention}.")


class AdminPromoModal(discord.ui.Modal):
    def __init__(self, promo: sqlite3.Row | None = None) -> None:
        title = "Modifier le code promo" if promo else "Ajouter un code promo"
        super().__init__(title=title)
        self.promo = promo
        self.code_input = discord.ui.TextInput(label="Code", max_length=30, default=promo["code"] if promo else "")
        self.percent_input = discord.ui.TextInput(label="Réduction % (0 = désactivé)", max_length=10, default=str(promo["discount_percent"]) if promo else "0")
        self.fixed_input = discord.ui.TextInput(label="Réduction fixe EUR (0 = désactivé)", max_length=10, default=str(promo["discount_fixed"]) if promo else "0")
        self.max_uses_input = discord.ui.TextInput(label="Utilisations max (0 = illimité)", max_length=10, default=str(promo["max_uses"]) if promo else "0")
        self.expires_input = discord.ui.TextInput(label="Date expiration (ISO ou vide)", max_length=30, default=promo["expires_at"] or "" if promo else "", required=False)
        self.add_item(self.code_input)
        self.add_item(self.percent_input)
        self.add_item(self.fixed_input)
        self.add_item(self.max_uses_input)
        self.add_item(self.expires_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        code = str(self.code_input).strip().upper()
        if not code:
            await interaction.response.send_message("❌ Code requis.", ephemeral=True)
            return
        try:
            percent = float(str(self.percent_input))
            fixed = float(str(self.fixed_input))
            max_uses = int(str(self.max_uses_input))
        except ValueError:
            await interaction.response.send_message("❌ Valeur invalide.", ephemeral=True)
            return
        expires = str(self.expires_input).strip() or None
        if self.promo:
            bot.db.update_promo_code(self.promo["id"], code=code, discount_percent=percent, discount_fixed=fixed, max_uses=max_uses, expires_at=expires)
            await interaction.response.send_message(f"✅ Code promo **{code}** modifié.", ephemeral=True)
        else:
            bot.db.add_promo_code(code=code, discount_percent=percent, discount_fixed=fixed, max_uses=max_uses, expires_at=expires)
            await interaction.response.send_message(f"✅ Code promo **{code}** ajouté.", ephemeral=True)
        log_channel = bot.get_channel(bot.settings.account_log_channel_id) if bot.settings.account_log_channel_id else None
        if log_channel and isinstance(log_channel, discord.TextChannel):
            action = "modifié" if self.promo else "ajouté"
            await log_channel.send(f"🎫 Code promo **{code}** {action} par {interaction.user.mention}.")


def is_founder(bot: EatZoneBot, member: discord.Member) -> bool:
    return is_admin(bot, member) or member_has_role(member, bot.settings.founder_role_ids)


class AdminPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Ajouter un compte", style=discord.ButtonStyle.success, emoji="➕", custom_id="ez:admin:add_account")
    async def add_account(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AdminAccountModal())

    @discord.ui.button(label="Modifier un compte", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="ez:admin:edit_account")
    async def edit_account(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        accounts = bot.db.list_accounts(available_only=False)
        if not accounts:
            await interaction.response.send_message("❌ Aucun compte.", ephemeral=True)
            return
        options = [discord.SelectOption(label=f"{a['name']} - {a['price']:.2f} EUR", value=str(a['id'])) for a in accounts]
        view = AdminAccountSelectView(options)
        await interaction.response.send_message("Choisis un compte à modifier :", view=view, ephemeral=True)

    @discord.ui.button(label="Supprimer un compte", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="ez:admin:del_account")
    async def delete_account(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        accounts = bot.db.list_accounts(available_only=False)
        if not accounts:
            await interaction.response.send_message("❌ Aucun compte.", ephemeral=True)
            return
        options = [discord.SelectOption(label=f"{a['name']} - {a['price']:.2f} EUR", value=str(a['id'])) for a in accounts]
        view = AdminAccountDeleteView(options)
        await interaction.response.send_message("Choisis un compte à supprimer :", view=view, ephemeral=True)

    @discord.ui.button(label="Activer/Désactiver compte", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="ez:admin:toggle_account")
    async def toggle_account(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        accounts = bot.db.list_accounts(available_only=False)
        if not accounts:
            await interaction.response.send_message("❌ Aucun compte.", ephemeral=True)
            return
        options = [discord.SelectOption(label=f"{a['name']} - {'✅' if a['available'] else '❌'} - {a['price']:.2f} EUR", value=str(a['id'])) for a in accounts]
        view = AdminAccountToggleView(options)
        await interaction.response.send_message("Choisis un compte à basculer :", view=view, ephemeral=True)

    @discord.ui.button(label="Ajouter code promo", style=discord.ButtonStyle.success, emoji="🎫", custom_id="ez:admin:add_promo")
    async def add_promo(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AdminPromoModal())

    @discord.ui.button(label="Supprimer code promo", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="ez:admin:del_promo")
    async def delete_promo(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        promos = bot.db.list_promo_codes()
        if not promos:
            await interaction.response.send_message("❌ Aucun code promo.", ephemeral=True)
            return
        options = [discord.SelectOption(label=f"{p['code']} - {p['used_count']}/{p['max_uses'] if p['max_uses'] > 0 else '∞'} utilisations", value=str(p['id'])) for p in promos]
        view = AdminPromoDeleteView(options)
        await interaction.response.send_message("Choisis un code promo à supprimer :", view=view, ephemeral=True)


class AdminAccountSelectView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminAccountSelect(options))


class AdminAccountSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Choisis un compte...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        account = bot.db.get_account(int(self.values[0]))
        if not account:
            await interaction.response.send_message("❌ Compte introuvable.", ephemeral=True)
            return
        await interaction.response.send_modal(AdminAccountModal(account=account))


class AdminAccountDeleteView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminAccountDelete(options))


class AdminAccountDelete(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Choisis un compte à supprimer...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        account = bot.db.get_account(int(self.values[0]))
        if not account:
            await interaction.response.send_message("❌ Compte introuvable.", ephemeral=True)
            return
        bot.db.delete_account(account["id"])
        await interaction.response.send_message(f"✅ Compte **{account['name']}** supprimé.", ephemeral=True)


class AdminAccountToggleView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminAccountToggle(options))


class AdminAccountToggle(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Choisis un compte...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        account = bot.db.get_account(int(self.values[0]))
        if not account:
            await interaction.response.send_message("❌ Compte introuvable.", ephemeral=True)
            return
        new_status = 0 if account["available"] else 1
        bot.db.update_account(account["id"], available=new_status)
        status_text = "✅ Disponible" if new_status else "❌ Indisponible"
        await interaction.response.send_message(f"✅ Compte **{account['name']}** → {status_text}", ephemeral=True)


class AdminPromoDeleteView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.add_item(AdminPromoDelete(options))


class AdminPromoDelete(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Choisis un code promo...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        promo_id = int(self.values[0])
        bot.db.delete_promo_code(promo_id)
        await interaction.response.send_message("✅ Code promo supprimé.", ephemeral=True)


class TicketControlsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Prendre en charge", style=discord.ButtonStyle.primary, emoji="👨‍🍳", custom_id="ez:ticket:claim")
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas accès à ce bouton.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        ticket = bot.db.find_ticket_for_channel(interaction.channel.id, interaction.channel.name)
        if not ticket:
            await interaction.response.send_message("❌ Ticket introuvable.", ephemeral=True)
            return
        pending_category_id = bot.settings.ticket_pending_category_id or bot.settings.ticket_category_id
        channel_is_pending = bool(pending_category_id and interaction.channel.category_id == pending_category_id)
        channel_looks_pending = bool(re.match(r"^commande-\d{1,6}$", interaction.channel.name.lower()))
        should_force_reclaim = (
            channel_is_pending
            or channel_looks_pending
            or str(ticket["status"]).lower() in {"open", "pending", "waiting"}
        )
        if ticket["claimed_by"] and should_force_reclaim:
            bot.db.unclaim_ticket(ticket["id"])
            ticket = bot.db.ticket_by_id(ticket["id"]) or ticket
        if ticket["claimed_by"]:
            await interaction.response.send_message(
                f"❌ Ce ticket est déjà pris en charge par <@{ticket['claimed_by']}>.",
                ephemeral=True,
            )
            return
        bot.db.claim_ticket(ticket["id"], interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message("✅ Ticket pris en charge.", ephemeral=True)
        asyncio.create_task(
            finalize_claim_ticket(
                bot,
                interaction.channel,
                interaction.guild,
                interaction.user.display_name,
                interaction.user.mention,
            )
        )

    @discord.ui.button(label="Remettre en attente", style=discord.ButtonStyle.secondary, emoji="↩️", custom_id="ez:ticket:unclaim")
    async def unclaim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Tu n'as pas accès à ce bouton.", ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return
        ticket = bot.db.find_ticket_for_channel(interaction.channel.id, interaction.channel.name)
        if not ticket:
            await interaction.response.send_message("❌ Ticket introuvable.", ephemeral=True)
            return
        if not ticket["claimed_by"]:
            await interaction.response.send_message("ℹ️ Ce ticket est déjà en attente.", ephemeral=True)
            return
        if int(ticket["claimed_by"]) != interaction.user.id and not is_admin(bot, interaction.user):
            await interaction.response.send_message("❌ Seul le cuisto qui l'a pris ou un admin peut le remettre en attente.", ephemeral=True)
            return
        bot.db.unclaim_ticket(ticket["id"])
        fresh_ticket = bot.db.ticket_by_id(ticket["id"])
        if fresh_ticket and fresh_ticket["claimed_by"]:
            bot.db.unclaim_ticket(ticket["id"])
        await interaction.response.send_message("✅ Ticket remis en attente.", ephemeral=True)
        asyncio.create_task(
            finalize_unclaim_ticket(
                bot,
                interaction.channel,
                interaction.guild,
                int(ticket["id"]),
            )
        )

    @discord.ui.button(label="PayPal", style=discord.ButtonStyle.secondary, emoji="💸", custom_id="ez:ticket:paypal")
    async def paypal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
            return
        await interaction.response.send_modal(PaymentModal(kind="paypal"))

    @discord.ui.button(label="Crypto", style=discord.ButtonStyle.secondary, emoji="🪙", custom_id="ez:ticket:crypto")
    async def crypto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
            return
        await interaction.response.send_modal(PaymentModal(kind="crypto"))

    @discord.ui.button(label="Revolut", style=discord.ButtonStyle.secondary, emoji="💳", custom_id="ez:ticket:revolut")
    async def revolut(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
            return
        await interaction.response.send_modal(PaymentModal(kind="revolut"))

    @discord.ui.button(label="Paysafe", style=discord.ButtonStyle.secondary, emoji="💴", custom_id="ez:ticket:paysafe")
    async def paysafe(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
            return
        await interaction.response.send_modal(PaymentModal(kind="paysafe"))

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ez:ticket:close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
            await interaction.response.send_message("❌ Seul le staff peut fermer ce ticket.", ephemeral=True)
            return
        await interaction.response.send_modal(CloseOrderModal())


class PaymentModal(discord.ui.Modal):
    def __init__(self, kind: str) -> None:
        titles = {"paypal": "Paiement PayPal", "revolut": "Paiement Revolut", "crypto": "Paiement Crypto", "paysafe": "Paiement Paysafecard"}
        super().__init__(title=titles.get(kind, "Paiement"))
        self.kind = kind
        self.amount = discord.ui.TextInput(label="Montant à payer", placeholder="Ex: 16.50", max_length=20)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: EatZoneBot = interaction.client  # type: ignore[assignment]
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
            return
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
            return
        try:
            amount = parse_amount(str(self.amount))
        except ValueError:
            await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
            return
        if self.kind == "paypal":
            embed = discord.Embed(title="Paiement PayPal", color=0x1ABC9C)
            embed.description = (
                f"Montant à payer : **{money(amount)}**\n\n"
                f"{bot.settings.paypal_text}\n\n"
                "Lien : https://www.paypal.me/SkyOress"
            )
            bot.db.create_payment(
                ticket_id=ticket["id"],
                channel_id=interaction.channel.id,
                kind="paypal",
                provider="manual",
                amount=amount,
                currency="EUR",
                status="pending",
                payment_url="https://www.paypal.me/SkyOress",
                external_id=None,
                created_by=interaction.user.id,
            )
            await interaction.response.send_message("✅ PayPal envoyé dans le ticket.", ephemeral=True)
            await interaction.channel.send(embed=embed)
            return

        if self.kind == "revolut":
            embed = discord.Embed(title="Paiement Revolut", color=0x9B59B6)
            embed.description = (
                f"Montant à payer : **{money(amount)}**\n\n"
                f"{bot.settings.revolut_text}\n\n"
                "Lien : https://revolut.me/nadegealine"
            )
            bot.db.create_payment(
                ticket_id=ticket["id"],
                channel_id=interaction.channel.id,
                kind="revolut",
                provider="manual",
                amount=amount,
                currency="EUR",
                status="pending",
                payment_url="https://revolut.me/nadegealine",
                external_id=None,
                created_by=interaction.user.id,
            )
            await interaction.response.send_message("✅ Revolut envoyé dans le ticket.", ephemeral=True)
            await interaction.channel.send(embed=embed)
            return

        if self.kind == "paysafe":
            embed = discord.Embed(title="Paiement PaysafeCard", color=0x3498DB)
            embed.description = (
                f"Montant à payer : **{money(amount)}**\n\n"
                f"{bot.settings.paysafe_text}\n\n"
                f"Lien : {bot.settings.paysafe_link}"
            )
            bot.db.create_payment(
                ticket_id=ticket["id"],
                channel_id=interaction.channel.id,
                kind="paysafe",
                provider="manual",
                amount=amount,
                currency="EUR",
                status="pending",
                payment_url=bot.settings.paysafe_link,
                external_id=None,
                created_by=interaction.user.id,
            )
            await interaction.response.send_message("✅ Paysafe envoyé dans le ticket.", ephemeral=True)
            await interaction.channel.send(embed=embed)
            return

        try:
            payment_url, external_id = await create_oxapay_invoice(bot.settings, amount, ticket["id"])
        except RuntimeError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        bot.db.create_payment(
            ticket_id=ticket["id"],
            channel_id=interaction.channel.id,
            kind="crypto",
            provider="oxapay",
            amount=amount,
            currency=bot.settings.oxapay_currency,
            status="pending",
            payment_url=payment_url,
            external_id=external_id,
            created_by=interaction.user.id,
        )
        embed = discord.Embed(title="Paiement Crypto", color=0xF39C12)
        embed.description = (
            f"Montant à payer : **{money(amount)}**\n"
            f"Cryptos acceptées : `{', '.join(bot.settings.oxapay_allowed_coins)}`\n"
            f"Expiration : **{bot.settings.oxapay_lifetime_minutes} min**\n\n"
            f"[Cliquer ici pour payer]({payment_url})"
        )
        await interaction.response.send_message("✅ Lien crypto envoyé dans le ticket.", ephemeral=True)
        await interaction.channel.send(embed=embed)


class CloseOrderModal(discord.ui.Modal, title="Fermer la commande"):
    order_cost = discord.ui.TextInput(
        label="Commande UberEats brute",
        placeholder="Ex: 10.00, ou mets R pour commande blanche",
        required=True,
        max_length=30,
    )
    resale = discord.ui.TextInput(
        label="Revente client",
        placeholder="Ex: 16.00, ou mets R pour fermer sans transaction",
        required=True,
        max_length=30,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_cost = str(self.order_cost).strip().lower()
        raw_resale = str(self.resale).strip().lower()
        blank = raw_cost in {"r", "0", "non", "none"} or raw_resale in {"r", "0", "non", "none"}
        if blank:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await close_current_ticket(interaction.client, interaction, order_cost=0, resale_amount=0, blank=True)  # type: ignore[arg-type]
            return
        try:
            order_cost = parse_amount(raw_cost)
            resale_amount = parse_amount(raw_resale)
        except ValueError:
            await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await close_current_ticket(
            interaction.client,  # type: ignore[arg-type]
            interaction,
            order_cost=order_cost,
            resale_amount=resale_amount,
            blank=False,
        )


async def close_current_ticket(
    bot: EatZoneBot,
    interaction: discord.Interaction,
    *,
    order_cost: float,
    resale_amount: float,
    blank: bool,
) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        return
    ticket = bot.db.ticket_by_channel(interaction.channel.id)
    if not ticket:
        await send_ephemeral(interaction, "❌ Ticket introuvable.")
        return
    profit = max(resale_amount - order_cost, 0.0)
    transcript = await create_transcript(bot, interaction.channel, int(ticket["id"]))
    bot.db.close_ticket(
        int(ticket["id"]),
        transcript_path=str(transcript) if transcript else None,
        order_cost=order_cost,
        resale_amount=resale_amount,
        profit_amount=profit,
        salary_amount=0,
    )
    fee_amount = ticket["fee_amount"] or 0
    summary = discord.Embed(title=f"Ticket fermé #{ticket['id']}", color=0xE74C3C)
    if blank:
        summary.description = "Commande blanche / sans transaction."
    else:
        summary.add_field(name="Commande brute", value=money(order_cost), inline=True)
        summary.add_field(name="Revente client", value=money(resale_amount), inline=True)
        if fee_amount > 0:
            summary.add_field(name="Frais", value=money(fee_amount), inline=True)
        summary.add_field(name="Bénéfice (100% cuisto)", value=money(profit), inline=True)
    if interaction.response.is_done():
        await interaction.followup.send(embed=summary, ephemeral=True)
    else:
        await interaction.response.send_message(embed=summary, ephemeral=True)
    await send_transcript(bot, interaction, transcript, int(ticket["creator_id"]))
    await asyncio.sleep(5)
    try:
        await interaction.channel.delete(reason="Ticket fermé")
    except discord.DiscordException:
        pass


async def create_transcript(bot: EatZoneBot, channel: discord.TextChannel, ticket_id: int) -> Path | None:
    bot.settings.transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = bot.settings.transcripts_dir / f"ticket-{ticket_id}.md"
    lines = [f"# Transcript ticket #{ticket_id}", ""]
    try:
        async for msg in channel.history(limit=500, oldest_first=True):
            author = f"{msg.author} ({msg.author.id})"
            content = msg.content or ""
            if msg.embeds:
                content += "\n" + "\n".join(embed.title or "Embed" for embed in msg.embeds)
            lines.append(f"[{msg.created_at.isoformat()}] {author}: {content}")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except discord.DiscordException:
        return None


async def send_transcript(bot: EatZoneBot, interaction: discord.Interaction, path: Path | None, creator_id: int) -> None:
    if not path or not path.exists():
        return
    file = discord.File(path)
    if bot.settings.transcript_channel_id and interaction.guild:
        channel = interaction.guild.get_channel(bot.settings.transcript_channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send("Transcript ticket fermé :", file=discord.File(path))
    user = bot.get_user(creator_id) or await bot.fetch_user(creator_id)
    try:
        await user.send("Voici la transcription de ton ticket :", file=file)
    except discord.DiscordException:
        pass


SERVICE_ROLE_ID = 1506032220660301838
SERVICE_CHANNEL_ID = 1510357586459758803


class InfluenceView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="OFF", style=discord.ButtonStyle.danger, emoji="🔴", custom_id="ez:inf:off")
    async def off(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await set_influence(interaction, "OFF", "🔴")

    @discord.ui.button(label="Attente", style=discord.ButtonStyle.secondary, emoji="🟠", custom_id="ez:inf:wait")
    async def wait(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await set_influence(interaction, "ATTENTE", "🟠")

    @discord.ui.button(label="Dispo", style=discord.ButtonStyle.success, emoji="🟢", custom_id="ez:inf:dispo")
    async def dispo(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await set_influence(interaction, "DISPO", "🟢")

    @discord.ui.button(label="Prendre service", style=discord.ButtonStyle.success, emoji="✅", custom_id="ez:service:start")
    async def start_service(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Commande serveur uniquement.", ephemeral=True)
            return
        guild = interaction.guild
        if not guild:
            return
        channel = guild.get_channel(SERVICE_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Salon de service introuvable.", ephemeral=True)
            return
        role = guild.get_role(SERVICE_ROLE_ID)
        ping = role.mention if role else f"<@&{SERVICE_ROLE_ID}>"
        await channel.send(f"{ping} **{interaction.user.display_name}** a pris son service et est dispo pour vos commandes !")
        await interaction.response.send_message("✅ Message de prise de service envoyé.", ephemeral=True)

    @discord.ui.button(label="Quitter", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="ez:service:end")
    async def end_service(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Commande serveur uniquement.", ephemeral=True)
            return
        guild = interaction.guild
        if not guild:
            return
        channel = guild.get_channel(SERVICE_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Salon de service introuvable.", ephemeral=True)
            return
        role = guild.get_role(SERVICE_ROLE_ID)
        ping = role.mention if role else f"<@&{SERVICE_ROLE_ID}>"
        await channel.send(f"{ping} **{interaction.user.display_name}** a quitté son service. Merci pour ton travail !")
        await interaction.response.send_message("✅ Message de fin de service envoyé.", ephemeral=True)


async def set_influence(interaction: discord.Interaction, status: str, emoji: str) -> None:
    bot: EatZoneBot = interaction.client  # type: ignore[assignment]
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return
    if not can_handle_orders(bot, interaction.user) and not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    record = bot.db.conn.execute("SELECT * FROM influence WHERE guild_id = ?", (interaction.guild.id,)).fetchone()
    channel = interaction.guild.get_channel(int(record["channel_id"])) if record else None
    if not isinstance(channel, discord.VoiceChannel):
        await interaction.response.send_message("❌ Salon statut introuvable. Repose le panel influence.", ephemeral=True)
        return
    name = f"𝑺𝑻𝑨𝑻𝑼𝑻 {emoji} {status}"
    try:
        await channel.edit(name=name, reason=f"Influence {status}")
    except discord.HTTPException as error:
        await interaction.response.send_message(f"❌ Discord bloque le rename pour l'instant: {error.text}", ephemeral=True)
        return
    bot.db.set_influence(interaction.guild.id, channel.id, status)
    await interaction.response.send_message(f"Affluence mise à jour: {emoji} {status}.", ephemeral=True)


async def create_oxapay_invoice(settings: Settings, amount: float, ticket_id: int) -> tuple[str, str | None]:
    if not settings.oxapay_api_key:
        raise RuntimeError("Clé OxaPay manquante dans les variables Railway.")
    payload: dict[str, Any] = {
        "amount": amount,
        "currency": settings.oxapay_currency,
        "lifetime": settings.oxapay_lifetime_minutes,
        "orderId": f"ticket-{ticket_id}-{int(datetime.now().timestamp())}",
        "description": f"Eat Zone ticket #{ticket_id}",
    }
    if settings.oxapay_allowed_coins:
        payload["coins"] = list(settings.oxapay_allowed_coins)
    header_attempts = [
        {"merchant_api_key": settings.oxapay_api_key},
        {"Authorization": f"Bearer {settings.oxapay_api_key}"},
    ]
    async with httpx.AsyncClient(timeout=20) as client:
        last_text = ""
        for headers in header_attempts:
            response = await client.post(settings.oxapay_invoice_url, json=payload, headers=headers)
            last_text = response.text[:300]
            if response.status_code in {401, 403}:
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"OxaPay refuse la requête ({response.status_code}). {last_text}")
            data = response.json()
            payment_url = (
                data.get("paymentUrl")
                or data.get("payLink")
                or data.get("url")
                or data.get("data", {}).get("paymentUrl")
                or data.get("data", {}).get("payLink")
                or data.get("data", {}).get("url")
            )
            external_id = (
                data.get("trackId")
                or data.get("id")
                or data.get("data", {}).get("trackId")
                or data.get("data", {}).get("id")
            )
            if payment_url:
                return str(payment_url), str(external_id) if external_id else None
        raise RuntimeError(f"OxaPay bloque la requête (403). Vérifie la clé API et les restrictions IP. {last_text}")


async def fetch_oxapay_status(settings: Settings, track_id: str) -> str | None:
    url = settings.oxapay_status_url.format(track_id=track_id)
    async with httpx.AsyncClient(timeout=15) as client:
        for headers in ({"merchant_api_key": settings.oxapay_api_key}, {"Authorization": f"Bearer {settings.oxapay_api_key}"}):
            try:
                response = await client.get(url, headers=headers)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue
            data = response.json()
            status = data.get("status") or data.get("data", {}).get("status")
            return str(status) if status else None
    return None


INFLUENCE_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "DISPO": ("🟢", "𝑫𝑰𝑺𝑷𝑶"),
    "ATTENTE": ("🟠", "𝑨𝑻𝑻𝑬𝑵𝑻𝑬"),
    "OFF": ("🔴", "𝑶𝑭𝑭"),
}


def influence_channel_name(status: str) -> str:
    emoji, label = INFLUENCE_STATUS_LABELS[status]
    return f"𝑺𝑻𝑨𝑻𝑼𝑻 {emoji} {label}"


def status_overwrites(guild: discord.Guild, *, visible: bool) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=visible, connect=False, speak=False)
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=False,
            speak=False,
            manage_channels=True,
        )
    return overwrites


def detect_influence_status(channel: discord.VoiceChannel) -> str | None:
    normalized = normalize_name(channel.name)
    if "statut" not in normalized:
        return None
    if "dispo" in normalized:
        return "DISPO"
    if "attente" in normalized:
        return "ATTENTE"
    if "off" in normalized:
        return "OFF"
    return None


def find_voice_status_channels(guild: discord.Guild, status: str) -> list[discord.VoiceChannel]:
    channels: list[discord.VoiceChannel] = []
    for channel in guild.voice_channels:
        if detect_influence_status(channel) == status:
            channels.append(channel)
    return sorted(channels, key=lambda item: (item.position, item.id))


async def remove_or_hide_status_duplicate(guild: discord.Guild, channel: discord.VoiceChannel) -> None:
    try:
        await channel.delete(reason="Nettoyage doublon salon statut Eat Zone")
        return
    except discord.DiscordException:
        pass
    try:
        await channel.edit(
            overwrites=status_overwrites(guild, visible=False),
            reason="Masquage doublon salon statut Eat Zone",
        )
    except discord.DiscordException:
        pass


async def ensure_influence_channels(guild: discord.Guild) -> dict[str, discord.VoiceChannel]:
    channels: dict[str, discord.VoiceChannel] = {}
    for status in INFLUENCE_STATUS_LABELS:
        existing_channels = find_voice_status_channels(guild, status)
        channel = existing_channels[0] if existing_channels else None
        for duplicate in existing_channels[1:]:
            await remove_or_hide_status_duplicate(guild, duplicate)
        if channel is None:
            channel = await guild.create_voice_channel(
                influence_channel_name(status),
                overwrites=status_overwrites(guild, visible=False),
                reason="Salon statut Eat Zone",
            )
        channels[status] = channel
    return channels


async def activate_influence_status(bot: EatZoneBot, guild: discord.Guild, status: str) -> discord.VoiceChannel:
    channels = await ensure_influence_channels(guild)
    kept_channel_ids = {channel.id for channel in channels.values()}
    for channel in list(guild.voice_channels):
        if channel.id not in kept_channel_ids and detect_influence_status(channel):
            await remove_or_hide_status_duplicate(guild, channel)

    for key, channel in channels.items():
        visible = key == status
        try:
            await channel.edit(
                name=influence_channel_name(key),
                overwrites=status_overwrites(guild, visible=visible),
                reason=f"Statut Eat Zone: {status}",
            )
        except discord.HTTPException:
            await channel.set_permissions(guild.default_role, view_channel=visible, connect=False, speak=False)
    active_channel = channels[status]
    bot.db.set_influence(guild.id, active_channel.id, status)
    return active_channel


async def set_influence(interaction: discord.Interaction, status: str, emoji: str) -> None:
    bot: EatZoneBot = interaction.client  # type: ignore[assignment]
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return
    if not can_handle_orders(bot, interaction.user) and not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    try:
        await activate_influence_status(bot, interaction.guild, status)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Il manque la permission Gerer les salons pour verrouiller le statut.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(f"✅ Statut mis a jour: {emoji} {status}.", ephemeral=True)


async def create_oxapay_invoice(settings: Settings, amount: float, ticket_id: int) -> tuple[str, str | None]:
    if not settings.oxapay_api_key:
        raise RuntimeError("Cle OxaPay manquante: ajoute OXAPAY_API_KEY dans Railway.")

    payload: dict[str, Any] = {
        "amount": amount,
        "currency": settings.oxapay_currency,
        "lifetime": settings.oxapay_lifetime_minutes,
        "order_id": f"ticket-{ticket_id}-{int(datetime.now().timestamp())}",
        "description": f"Eat Zone ticket #{ticket_id}",
        "fee_paid_by_payer": 1,
        "mixed_payment": False,
        "sandbox": False,
    }

    headers = {
        "merchant_api_key": settings.oxapay_api_key,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(settings.oxapay_invoice_url, json=payload, headers=headers)
        body_preview = response.text[:300]
        if response.status_code == 401:
            raise RuntimeError(
                "OxaPay dit que la Merchant API Key est invalide. Mets la vraie cle Merchant API dans Railway > OXAPAY_API_KEY."
            )
        if response.status_code == 403:
            raise RuntimeError(
                "OxaPay bloque la requete. Verifie les restrictions IP/Cloudflare dans ton compte OxaPay."
            )
        if response.status_code >= 400:
            raise RuntimeError(f"OxaPay refuse la requete ({response.status_code}). {body_preview}")

        data = response.json()
        data_block = data.get("data") if isinstance(data.get("data"), dict) else {}
        payment_url = (
            data_block.get("payment_url")
            or data_block.get("paymentUrl")
            or data_block.get("payLink")
            or data.get("payment_url")
            or data.get("paymentUrl")
            or data.get("payLink")
            or data.get("url")
        )
        external_id = data_block.get("track_id") or data_block.get("trackId") or data.get("track_id") or data.get("trackId")
        if not payment_url:
            raise RuntimeError(f"OxaPay n'a pas renvoye de lien de paiement. Reponse: {body_preview}")
        return str(payment_url), str(external_id) if external_id else None


async def fetch_oxapay_status(settings: Settings, track_id: str) -> str | None:
    url = settings.oxapay_status_url.format(track_id=track_id)
    headers = {
        "merchant_api_key": settings.oxapay_api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        data = response.json()
        data_block = data.get("data") if isinstance(data.get("data"), dict) else {}
        status = data_block.get("status") or data.get("status")
        return str(status) if status else None


async def close_current_ticket(
    bot: EatZoneBot,
    interaction: discord.Interaction,
    *,
    order_cost: float,
    resale_amount: float,
    blank: bool,
) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        return
    ticket = bot.db.ticket_by_channel(interaction.channel.id)
    if not ticket:
        await send_ephemeral(interaction, "❌ Ticket introuvable.")
        return

    profit = max(resale_amount - order_cost, 0.0)

    transcript = await create_transcript(bot, interaction.channel, int(ticket["id"]))
    bot.db.close_ticket(
        int(ticket["id"]),
        transcript_path=str(transcript) if transcript else None,
        order_cost=order_cost,
        resale_amount=resale_amount,
        profit_amount=profit,
        salary_amount=0,
    )

    fee_amount = ticket["fee_amount"] or 0
    summary = discord.Embed(title=f"Ticket fermé #{ticket['id']}", color=0xE74C3C)
    if blank:
        summary.description = "Commande blanche / sans transaction."
    else:
        summary.add_field(name="Commande brute", value=money(order_cost), inline=True)
        summary.add_field(name="Revente client", value=money(resale_amount), inline=True)
        if fee_amount > 0:
            summary.add_field(name="Frais", value=money(fee_amount), inline=True)
        summary.add_field(name="Benefice (100% cuisto)", value=money(profit), inline=True)
    if interaction.response.is_done():
        await interaction.followup.send(embed=summary, ephemeral=True)
    else:
        await interaction.response.send_message(embed=summary, ephemeral=True)
    await send_transcript(bot, interaction, transcript, int(ticket["creator_id"]))
    await asyncio.sleep(5)
    try:
        await interaction.channel.delete(reason="Ticket ferme avec transcript")
    except discord.DiscordException:
        pass


settings = load_settings()
db = Database(settings.database_path)
bot = EatZoneBot(settings, db)


@bot.event
async def on_ready() -> None:
    print(f"Connecté en tant que {bot.user} ({bot.user.id if bot.user else 'no-id'})")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    role_id = bot.settings.verified_role_id
    channel_id = bot.settings.welcome_channel_id
    if not role_id or not channel_id:
        return
    before_roles = {role.id for role in before.roles}
    after_roles = {role.id for role in after.roles}
    if role_id not in before_roles and role_id in after_roles:
        channel = after.guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(f"{after.mention} nous a rejoint. Nous sommes désormais **{after.guild.member_count}** sur le serveur !")


panel_choice = [
    app_commands.Choice(name="commande", value="commande"),
    app_commands.Choice(name="support", value="support"),
    app_commands.Choice(name="influence", value="influence"),
    app_commands.Choice(name="compte", value="compte"),
    app_commands.Choice(name="basicfit", value="basicfit"),
    app_commands.Choice(name="cuisto", value="cuisto"),
]


@bot.tree.command(name="panel", description="Pose un panel commande, support, influence ou compte.")
@app_commands.choices(panel_type=panel_choice)
async def panel(interaction: discord.Interaction, panel_type: app_commands.Choice[str], channel: discord.TextChannel) -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Fondateur/admin uniquement.", ephemeral=True)
        return
    if panel_type.value == "commande":
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Commander",
            description=(
                "Clique sur le bouton pour ouvrir le formulaire de commande.\n\n"
                "Le ticket sera range automatiquement dans **Commande non traiter**, puis dans "
                "**Commande en cours** quand un cuisto le prend."
            ),
            color=0x2ECC71,
        )
        embed.add_field(name="Infos demandees", value="Adresse, restaurant, panier HT/TTC et moyen de paiement.", inline=False)
        await channel.send(embed=embed, view=CommandPanelView())
    elif panel_type.value == "support":
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Support",
            description=(
                "Clique sur le bouton pour ouvrir un ticket support.\n\n"
                "Panel support simple: le staff peut seulement fermer le ticket."
            ),
            color=0x3498DB,
        )
        await channel.send(embed=embed, view=SupportPanelView())
    elif panel_type.value == "compte":
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Achat de comptes",
            description=(
                "Clique sur le bouton pour voir les comptes disponibles.\n\n"
                "Les ventes sont traitées manuellement par notre équipe. "
                "Les produits peuvent ne pas être disponibles immédiatement."
            ),
            color=0x9B59B6,
        )
        embed.add_field(
            name="⚠️ Garantie",
            value=(
                "La garantie des comptes nécessite obligatoirement la **tech Ub*r**.\n\n"
                "**Aucun remplacement ne sera proposé** si le compte flag, "
                "ou si le contenu promis n'est pas sur le compte (avec preuve).\n"
                "Les comptes sont vérifiés à l'avance."
            ),
            inline=False,
        )
        await channel.send(embed=embed, view=AccountPanelView())
    elif panel_type.value == "basicfit":
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Basic-Fit Ultimate",
            description=basicfit.PANEL_DESCRIPTION,
            color=basicfit.BASICFIT_COLOR,
        )
        await channel.send(embed=embed, view=basicfit.BasicFitPanelView())
    elif panel_type.value == "cuisto":
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Devenir Cuisto",
            description=(
                "Tu veux devenir cuisto chez nous ?\n\n"
                "**\U0001f4b0 Abonnement hebdomadaire**\n"
                "Le prix varie selon l'affluence du serveur.\n\n"
                "**\U0001f4aa Avantages :**\n"
                "\u2022 Tu **gardes 100% des benefices** sur tes commandes\n"
                "\u2022 Code promo exclusif sur les comptes\n"
                "\u2022 Pas de partage, tout est pour toi\n\n"
                "\u26a0\ufe0f **Requis :**\n"
                "\u2022 Tu dois etre detenteur de la **Tech Uber** pour devenir cuisto\n"
                "\u2022 Si tu ne l'as pas encore, achete-la ici : <#1514065238243414066>\n\n"
                "Clique sur le bouton ci-dessous pour voir les offres !"
            ),
            color=cuisto.CUISTO_COLOR,
        )
        await channel.send(embed=embed, view=cuisto.CuistoPanelView())
    else:
        if interaction.guild:
            await activate_influence_status(bot, interaction.guild, "DISPO")
        embed = discord.Embed(
            title=f"{bot.settings.brand_name} | Affluence",
            description=(
                "Change le statut visible en haut du serveur.\n\n"
                "🟢 **DISPO**: les commandes partent vite.\n"
                "🟠 **ATTENTE**: il y a un peu de queue.\n"
                "🔴 **OFF**: ferme ou indisponible.\n\n"
                "Les salons statut sont verrouilles: visibles pour informer, impossibles a rejoindre."
            ),
            color=0x2ECC71,
        )
        await channel.send(embed=embed, view=InfluenceView())
    await interaction.response.send_message("✅ Panel posé.", ephemeral=True)


@bot.tree.command(name="paneladmin", description="Pose le panneau d'administration des comptes.")
async def paneladmin(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not isinstance(interaction.user, discord.Member) or not is_founder(bot, interaction.user):
        await interaction.response.send_message("❌ Fondateur uniquement.", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"{bot.settings.brand_name} | Administration des comptes",
        description="Gère les comptes et codes promo via les boutons ci-dessous.",
        color=0xE74C3C,
    )
    await channel.send(embed=embed, view=AdminPanelView())
    await interaction.response.send_message("✅ Panel admin posé.", ephemeral=True)


@bot.tree.command(name="basicfit", description="Pose le panel Basic-Fit Ultimate.")
@app_commands.checks.has_permissions(administrator=True)
async def basicfit_panel(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"{bot.settings.brand_name} | Basic-Fit Ultimate",
        description=basicfit.PANEL_DESCRIPTION,
        color=basicfit.BASICFIT_COLOR,
    )
    await channel.send(embed=embed, view=basicfit.BasicFitPanelView())
    await interaction.response.send_message("✅ Panel Basic-Fit pose.", ephemeral=True)


@bot.tree.command(name="basicfitadmin", description="Pose le panneau d'administration Basic-Fit.")
async def basicfit_admin(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Administration Basic-Fit",
        description="Gere les comptes, envoie des annonces et consulte les stats.",
        color=basicfit.BASICFIT_COLOR,
    )
    await channel.send(embed=embed, view=basicfit.BasicFitAdminView())
    await interaction.response.send_message("✅ Panel admin Basic-Fit pose.", ephemeral=True)


@bot.tree.command(name="cuistoadmin", description="Pose le panneau d'administration Cuisto.")
async def cuisto_admin(interaction: discord.Interaction, channel: discord.TextChannel) -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
        return
    embed = discord.Embed(
        title="Administration Cuisto",
        description=(
            "Configure les prix de l'abonnement cuisto et consulte les statistiques.\n\n"
            "**Prix selon l'affluence :**\n"
            "\u2022 \U0001f7e2 **DISPO** - Prix normal\n"
            "\u2022 \U0001f7e0 **ATTENTE** - Prix plus eleve (forte demande)\n"
            "\u2022 \U0001f534 **OFF** - Inscriptions fermees"
        ),
        color=cuisto.CUISTO_COLOR,
    )
    await channel.send(embed=embed, view=cuisto.CuistoAdminView())
    await interaction.response.send_message("✅ Panel admin Cuisto pose.", ephemeral=True)


async def ensure_ticket_command(interaction: discord.Interaction) -> sqlite3.Row | None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
        return None
    ticket = bot.db.ticket_by_channel(interaction.channel.id)
    if not ticket:
        await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
        return None
    return ticket


@bot.tree.command(name="paypal", description="Envoie un paiement PayPal dans le ticket.")
async def paypal(interaction: discord.Interaction, montant: str) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
        await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
        return
    ticket = await ensure_ticket_command(interaction)
    if not ticket:
        return
    try:
        amount = parse_amount(montant)
    except ValueError:
        await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        return
    embed = discord.Embed(title="Paiement PayPal", color=0x1ABC9C)
    embed.description = f"Montant à payer : **{money(amount)}**\n\n{bot.settings.paypal_text}\n\nLien : https://www.paypal.me/SkyOress"
    bot.db.create_payment(
        ticket_id=ticket["id"],
        channel_id=interaction.channel.id,
        kind="paypal",
        provider="manual",
        amount=amount,
        currency="EUR",
        status="pending",
        payment_url="https://www.paypal.me/SkyOress",
        external_id=None,
        created_by=interaction.user.id,
    )
    await interaction.response.send_message("✅ PayPal envoyé.", ephemeral=True)
    await interaction.channel.send(embed=embed)


@bot.tree.command(name="revolut", description="Envoie un paiement Revolut dans le ticket.")
async def revolut(interaction: discord.Interaction, montant: str) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
        await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
        return
    ticket = await ensure_ticket_command(interaction)
    if not ticket:
        return
    try:
        amount = parse_amount(montant)
    except ValueError:
        await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        return
    embed = discord.Embed(title="Paiement Revolut", color=0x9B59B6)
    embed.description = f"Montant à payer : **{money(amount)}**\n\n{bot.settings.revolut_text}\n\nLien : https://revolut.me/nadegealine"
    bot.db.create_payment(
        ticket_id=ticket["id"],
        channel_id=interaction.channel.id,
        kind="revolut",
        provider="manual",
        amount=amount,
        currency="EUR",
        status="pending",
        payment_url="https://revolut.me/nadegealine",
        external_id=None,
        created_by=interaction.user.id,
    )
    await interaction.response.send_message("✅ Revolut envoyé.", ephemeral=True)
    await interaction.channel.send(embed=embed)


@bot.tree.command(name="crypto", description="Cree un lien de paiement crypto dans le ticket.")
async def crypto(interaction: discord.Interaction, montant: str) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
        await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
        return
    ticket = await ensure_ticket_command(interaction)
    if not ticket:
        return
    try:
        amount = parse_amount(montant)
        payment_url, external_id = await create_oxapay_invoice(bot.settings, amount, ticket["id"])
    except (ValueError, RuntimeError) as error:
        await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        return
    bot.db.create_payment(
        ticket_id=ticket["id"],
        channel_id=interaction.channel.id,
        kind="crypto",
        provider="oxapay",
        amount=amount,
        currency=bot.settings.oxapay_currency,
        status="pending",
        payment_url=payment_url,
        external_id=external_id,
        created_by=interaction.user.id,
    )
    embed = discord.Embed(title="Paiement Crypto", color=0xF39C12)
    embed.description = f"Montant à payer : **{money(amount)}**\n\n[Cliquer ici pour payer]({payment_url})"
    await interaction.response.send_message("✅ Lien crypto envoyé.", ephemeral=True)
    await interaction.channel.send(embed=embed)


@bot.tree.command(name="frais", description="Ajoute des frais à un mauvais client.")
async def frais(interaction: discord.Interaction, client: discord.Member, montant: str, raison: str) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
        await interaction.response.send_message("❌ Réservé au staff.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Commande uniquement dans un ticket.", ephemeral=True)
        return
    try:
        amount = parse_amount(montant)
    except ValueError:
        await interaction.response.send_message("❌ Montant invalide.", ephemeral=True)
        return
    if amount < 0:
        await interaction.response.send_message("❌ Le montant ne peut pas être négatif.", ephemeral=True)
        return
    embed = discord.Embed(title="💸 Frais ajoutés", color=0xE74C3C)
    embed.add_field(name="Client", value=client.mention, inline=False)
    embed.add_field(name="Montant", value=money(amount), inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.set_footer(text=f"Ajouté par {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    try:
        await client.send(f"💸 **Des frais de {money(amount)}** ont été ajoutés à ton ticket.\n**Raison :** {raison}")
    except discord.DiscordException:
        pass


@bot.tree.command(name="confirm", description="Confirme manuellement le paiement du ticket.")
async def confirm(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_orders(bot, interaction.user):
        await interaction.response.send_message("❌ Réservé au staff paiement.", ephemeral=True)
        return
    ticket = await ensure_ticket_command(interaction)
    if not ticket:
        return
    if ticket["ticket_type"] == "basicfit":
        await interaction.response.send_message("Confirmation Basic-Fit en cours...", ephemeral=True)
        await basicfit.handle_basicfit_confirm(bot, interaction, ticket)
        return
    await interaction.response.send_message("Confirmation envoyee.", ephemeral=True)
    if ticket["ticket_type"] == "account":
        msg = "Paiement confirme. Tu vas recevoir ton compte sous 5 minutes."
    else:
        msg = bot.settings.payment_confirmed_message
    await interaction.channel.send(msg)
    if isinstance(interaction.channel, discord.TextChannel):
        asyncio.create_task(
            move_ticket_to_paid_category(bot, interaction.channel, interaction.guild, int(ticket["id"]))
        )


@bot.tree.command(name="avis", description="Demande un avis au client et ajoute le role client fidele.")
async def avis(interaction: discord.Interaction, membre: discord.Member) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("❌ Commande serveur uniquement.", ephemeral=True)
        return
    if not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return

    target = membre

    if target is None:
        await interaction.response.send_message(
            "❌ Mentionne le client avec `/avis membre:@client`.",
            ephemeral=True,
        )
        return

    loyalty_role_id = 1506012403672940568
    review_channel_id = 1506012404662665462

    role_added = False
    role = interaction.guild.get_role(loyalty_role_id)
    if role:
        try:
            await target.add_roles(role, reason="Client fidele apres avis")
            role_added = True
        except discord.DiscordException:
            role_added = False

    review_target = f"<#{review_channel_id}>"
    review_link = f"https://discord.com/channels/{interaction.guild.id}/{review_channel_id}"
    message = (
        f"{target.mention} merci pour ta commande !\n"
        f"N'oublie pas de déposer un avis ici : {review_target}\n"
        f"{review_link}"
    )

    await interaction.response.send_message(
        "✅ Demande d'avis envoyée." + (" Rôle client fidèle ajouté." if role_added else ""),
        ephemeral=True,
    )
    if isinstance(interaction.channel, discord.TextChannel):
        await interaction.channel.send(message)
    else:
        try:
            await target.send(message)
        except discord.DiscordException:
            pass


@bot.tree.command(name="lock", description="Verrouille le salon.")
async def lock(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    if isinstance(interaction.channel, discord.TextChannel):
        everyone = interaction.guild.default_role
        for target, overwrite in interaction.channel.overwrites.items():
            if isinstance(target, discord.Role) and target != everyone and overwrite.send_messages is True:
                overwrite.send_messages = False
                overwrite.create_public_threads = False
                overwrite.create_private_threads = False
                overwrite.send_messages_in_threads = False
                await interaction.channel.set_permissions(target, overwrite=overwrite)
                blocked = True
        await interaction.channel.set_permissions(
            everyone,
            send_messages=False,
            create_public_threads=False,
            create_private_threads=False,
            send_messages_in_threads=False,
        )
        await interaction.response.send_message("🔒 Salon verrouillé.", ephemeral=True)


@bot.tree.command(name="unlock", description="Déverrouille le salon.")
async def unlock(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    if isinstance(interaction.channel, discord.TextChannel):
        everyone = interaction.guild.default_role
        for target, overwrite in interaction.channel.overwrites.items():
            if isinstance(target, discord.Role) and target != everyone and overwrite.send_messages is False:
                overwrite.send_messages = None
                overwrite.create_public_threads = None
                overwrite.create_private_threads = None
                overwrite.send_messages_in_threads = None
                await interaction.channel.set_permissions(target, overwrite=overwrite)
        await interaction.channel.set_permissions(
            everyone,
            send_messages=None,
            create_public_threads=None,
            create_private_threads=None,
            send_messages_in_threads=None,
        )
        await interaction.response.send_message("🔓 Salon déverrouillé.", ephemeral=True)


@bot.tree.command(name="nuke", description="Supprime les derniers messages du salon.")
async def nuke(interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 100] = 50) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    if isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=nombre)
        await interaction.followup.send(f"✅ {len(deleted)} message(s) supprimés.", ephemeral=True)


@bot.tree.command(name="ban", description="Bannit un membre.")
async def ban(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison") -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
        return
    await membre.ban(reason=raison)
    await interaction.response.send_message(f"✅ {membre} banni.", ephemeral=True)


@bot.tree.command(name="kick", description="Expulse un membre.")
async def kick(interaction: discord.Interaction, membre: discord.Member, raison: str = "Aucune raison") -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
        return
    await membre.kick(reason=raison)
    await interaction.response.send_message(f"✅ {membre} expulsé.", ephemeral=True)


@bot.tree.command(name="mute", description="Timeout un membre.")
async def mute(
    interaction: discord.Interaction,
    membre: discord.Member,
    minutes: app_commands.Range[int, 1, 40320] = 30,
    raison: str = "Mute",
) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    try:
        await membre.timeout(datetime.now(timezone.utc) + timedelta(minutes=int(minutes)), reason=raison)
    except discord.DiscordException:
        await interaction.response.send_message("❌ Impossible de mute ce membre. Vérifie la hiérarchie des rôles du bot.", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ {membre.mention} mute {minutes} min.", ephemeral=True)


@bot.tree.command(name="exclure", description="Exclut temporairement un membre sans le kick.")
async def exclure(
    interaction: discord.Interaction,
    membre: discord.Member,
    minutes: app_commands.Range[int, 1, 40320] = 60,
    raison: str = "Exclusion temporaire",
) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    try:
        until = datetime.now(timezone.utc) + timedelta(minutes=int(minutes))
        await membre.timeout(until, reason=raison)
    except discord.DiscordException:
        await interaction.response.send_message("❌ Impossible d'exclure ce membre. Vérifie la hiérarchie des rôles du bot.", ephemeral=True)
        return
    await interaction.response.send_message(f"✅ {membre.mention} exclu temporairement {minutes} min.", ephemeral=True)


@bot.tree.command(name="unmute", description="Retire le timeout d'un membre.")
async def unmute(interaction: discord.Interaction, membre: discord.Member) -> None:
    if not isinstance(interaction.user, discord.Member) or not can_handle_support(bot, interaction.user):
        await interaction.response.send_message("❌ Staff uniquement.", ephemeral=True)
        return
    await membre.timeout(None)
    await interaction.response.send_message(f"✅ {membre.mention} unmute.", ephemeral=True)


@bot.tree.command(name="resume", description="Affiche les infos du ticket en texte pour copier.")
async def resume(interaction: discord.Interaction) -> None:
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ Utilisable uniquement dans un ticket.", ephemeral=True)
        return
    ticket = bot.db.find_ticket_for_channel(interaction.channel.id, interaction.channel.name)
    if not ticket:
        await interaction.response.send_message("❌ Ticket introuvable.", ephemeral=True)
        return

    lines = [
        f"Ticket #{ticket['id']}",
        f"Type : {ticket['ticket_type']}",
        f"Client : {ticket['creator_name']} (ID: {ticket['creator_id']})",
        f"Statut : {ticket['status']}",
    ]
    if ticket["claimed_by"]:
        lines.append(f"Pris par : {ticket['claimed_name'] or '?'}")
    if ticket["address"]:
        lines.append(f"Adresse : {ticket['address']}")
    if ticket["restaurant"]:
        lines.append(f"Restaurant : {ticket['restaurant']}")
    if ticket["amount_ht"]:
        lines.append(f"Montant HT : {money(ticket['amount_ht'])}")
    if ticket["amount_ttc"]:
        lines.append(f"Montant TTC : {money(ticket['amount_ttc'])}")
    if ticket["payment_method"]:
        lines.append(f"Paiement : {ticket['payment_method']}")
    fee_amount = ticket["fee_amount"] or 0
    if fee_amount > 0:
        lines.append(f"Frais : {money(fee_amount)}")

    await interaction.response.send_message("```\n" + "\n".join(lines) + "\n```", ephemeral=True)


@bot.tree.command(name="annonce", description="Poste une annonce dans un salon (au nom du bot).")
async def annonce(
    interaction: discord.Interaction,
    salon: discord.TextChannel,
    message: str,
) -> None:
    if not isinstance(interaction.user, discord.Member) or not is_admin(bot, interaction.user):
        await interaction.response.send_message("❌ Admin uniquement.", ephemeral=True)
        return
    try:
        embed = discord.Embed(
            title="📢 Annonce",
            description=message,
            color=0x2ECC71,
        )
        await salon.send(embed=embed)
        await interaction.response.send_message(f"✅ Annonce postée dans {salon.mention}.", ephemeral=True)
    except discord.DiscordException:
        await interaction.response.send_message("❌ Impossible d'envoyer dans ce salon.", ephemeral=True)


if not settings.token:
    raise RuntimeError("DISCORD_TOKEN manquant. Ajoute-le dans Railway > Variables.")

bot.run(settings.token)
