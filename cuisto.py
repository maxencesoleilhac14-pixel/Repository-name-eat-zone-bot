from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone, timedelta

import discord
import httpx
from discord.ext import commands

APPRENTI_ROLE_ID = 1506012403672940570
CUISTO_ROLE_ID = 1506012403672940566
CUISTO_COLOR = 0xE67E22
WEEKS_TO_MASTER = 4


def init_cuisto_tables(db) -> None:
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS cuisto_settings (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            weekly_price REAL NOT NULL DEFAULT 15.00,
            attente_price REAL NOT NULL DEFAULT 20.00,
            off_price REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT ''
        );
        INSERT OR IGNORE INTO cuisto_settings (id, weekly_price, attente_price, off_price, updated_at) VALUES (1, 15.00, 20.00, 0, '');

        CREATE TABLE IF NOT EXISTS cuisto_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT NOT NULL DEFAULT '',
            week_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'paid',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cuisto_subscriptions (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT NOT NULL,
            role_level TEXT NOT NULL DEFAULT 'apprentice',
            current_week INTEGER NOT NULL DEFAULT 0,
            last_payment_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT
        );
    """)
    db.conn.commit()
    try:
        db.conn.execute("ALTER TABLE cuisto_subscriptions ADD COLUMN grace_until TEXT")
        db.conn.commit()
    except Exception:
        pass


def get_cuisto_prices(db) -> dict:
    row = db.conn.execute("SELECT weekly_price, attente_price, off_price FROM cuisto_settings WHERE id = 1").fetchone()
    if not row:
        return {"dispo": 15.0, "attente": 20.0, "off": 0.0}
    return {"dispo": row["weekly_price"], "attente": row["attente_price"], "off": row["off_price"]}


def update_cuisto_prices(db, weekly_price: float, attente_price: float, off_price: float) -> None:
    db.conn.execute(
        "UPDATE cuisto_settings SET weekly_price = ?, attente_price = ?, off_price = ?, updated_at = ? WHERE id = 1",
        (weekly_price, attente_price, off_price, datetime.now(timezone.utc).isoformat()),
    )
    db.conn.commit()


def get_price_for_influence(db, influence_status: str | None) -> float:
    prices = get_cuisto_prices(db)
    if influence_status == "ATTENTE":
        return prices["attente"]
    if influence_status == "OFF":
        return prices["off"]
    return prices["dispo"]


def get_user_subscription(db, user_id: int):
    return db.conn.execute(
        "SELECT * FROM cuisto_subscriptions WHERE user_id = ?", (user_id,)
    ).fetchone()


def get_user_payment_count(db, user_id: int) -> int:
    row = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM cuisto_payments WHERE user_id = ? AND status = 'paid'",
        (user_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def record_cuisto_payment(db, *, user_id: int, user_name: str, amount: float, payment_method: str, week_number: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.conn.execute(
        "INSERT INTO cuisto_payments (user_id, user_name, amount, payment_method, week_number, status, created_at) VALUES (?, ?, ?, ?, ?, 'paid', ?)",
        (user_id, user_name, amount, payment_method, week_number, now),
    )
    db.conn.commit()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    level = "master" if week_number >= WEEKS_TO_MASTER else "apprentice"
    db.conn.execute(
        """INSERT INTO cuisto_subscriptions (user_id, user_name, role_level, current_week, last_payment_at, active, expires_at, grace_until)
           VALUES (?, ?, ?, ?, ?, 1, ?, NULL)
           ON CONFLICT(user_id) DO UPDATE SET
               user_name = excluded.user_name,
               role_level = CASE WHEN excluded.role_level = 'master' THEN 'master' ELSE cuisto_subscriptions.role_level END,
               current_week = excluded.current_week,
               last_payment_at = excluded.last_payment_at,
               active = 1,
               expires_at = excluded.expires_at,
               grace_until = NULL""",
        (user_id, user_name, level, week_number, now, expires),
    )
    db.conn.commit()


def normalize_name(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = value.replace("é", "e").replace("è", "e").replace("ê", "e").replace("à", "a")
    return re.sub(r"[^a-z0-9]+", "", value)


async def send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def get_current_influence(bot, guild_id: int) -> str | None:
    record = bot.db.conn.execute(
        "SELECT status FROM influence WHERE guild_id = ?", (guild_id,)
    ).fetchone()
    if record:
        return record["status"]
    return None


async def find_or_create_cuisto_category(guild: discord.Guild, name: str, candidates: tuple[str, ...]) -> discord.CategoryChannel:
    normalized = {c.lower().replace(" ", "").replace("-", "") for c in candidates}
    for cat in guild.categories:
        n = normalize_name(cat.name)
        if n in normalized:
            return cat
        if any(c in n for c in normalized):
            return cat
    return await guild.create_category(name, reason="Categorie Cuisto")


async def make_cuisto_overwrites(bot, guild: discord.Guild, user: discord.Member) -> dict:
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    chef_role = guild.get_role(bot.settings.maitre_cuisto_role_id) if bot.settings.maitre_cuisto_role_id else None
    if chef_role:
        overwrites[chef_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for role_id in bot.settings.admin_role_ids:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
    founder_ids = getattr(bot.settings, 'founder_role_ids', ())
    for role_id in founder_ids:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    return overwrites


def is_cuisto_staff(bot, member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    for role_id in bot.settings.admin_role_ids:
        if member.get_role(role_id):
            return True
    founder_ids = getattr(bot.settings, 'founder_role_ids', ())
    for role_id in founder_ids:
        if member.get_role(role_id):
            return True
    if bot.settings.maitre_cuisto_role_id and member.get_role(bot.settings.maitre_cuisto_role_id):
        return True
    return False


class CuistoPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Devenir Cuisto", style=discord.ButtonStyle.success, emoji="\U0001f468\u200d\U0001f373", custom_id="ez:cuisto:become")
    async def become_cuisto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        influence = await get_current_influence(bot, interaction.guild.id)
        price = get_price_for_influence(bot.db, influence)

        if price <= 0:
            await send_ephemeral(interaction, "\U0001f6ab Les inscriptions cuisto sont fermees pour le moment (statut OFF).")
            return

        sub = get_user_subscription(bot.db, interaction.user.id)
        week_count = (sub["current_week"] if sub else 0) + 1
        next_level = "Maitre Cuisto" if week_count >= WEEKS_TO_MASTER else f"Apprenti (Semaine {week_count}/{WEEKS_TO_MASTER})"

        influence_label = {"DISPO": "\U0001f7e2 Dispo", "ATTENTE": "\U0001f7e0 Attente", "OFF": "\U0001f534 OFF"}.get(influence or "", "Dispo")

        ticket_id = bot.db.create_ticket(
            guild_id=interaction.guild.id,
            creator_id=interaction.user.id,
            creator_name=str(interaction.user),
            ticket_type="cuisto",
            address=f"Abonnement cuisto semaine {week_count}",
            payment_enabled=True,
        )

        category = await find_or_create_cuisto_category(
            interaction.guild,
            "Cuisto",
            ("cuisto", "abonnement cuisto", "bf achat"),
        )
        overwrites = await make_cuisto_overwrites(bot, interaction.guild, interaction.user)
        channel = await interaction.guild.create_text_channel(
            name=f"cuisto-{ticket_id:04d}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket Cuisto #{ticket_id}",
        )
        bot.db.attach_channel(ticket_id, channel.id)

        embed = discord.Embed(
            title=f"\U0001f468\u200d\U0001f373 Abonnement Cuisto #{ticket_id}",
            description=(
                "Souscris a l'abonnement cuisto pour pouvoir prendre des commandes !\n\n"
                f"\U0001f4b0 **Prix : {price:.2f} EUR / semaine** (selon affluence)\n"
                f"\U0001f7e2 Affluence actuelle : **{influence_label}**\n\n"
                "\U0001f504 **Fonctionnement :**\n"
                "\u2022 Paiement **hebdomadaire** requis\n"
                "\u2022 Role **Apprenti** attribue des la 1ere semaine\n"
                f"\u2022 Apres **{WEEKS_TO_MASTER} semaines** de paiement -> role **Maitre Cuisto**\n"
                "\u2022 Apres 7 jours, roles retires mais **1 jour de grace** pour renouveler sans perdre la progression\n"
                "\u2022 Si tu depasses le delai, le compteur repart a zero\n\n"
                "\U0001f4b0 **Avantages :**\n"
                "\u2022 \U0001f3e0 Tu **gardes 100% des benefices** sur tes commandes !\n"
                "\u2022 \U0001f6ab **Aucun partage** avec personne, tout est pour toi\n"
                "\u2022 \U0001f9f0 **Code promo exclusif** sur les comptes (reduction speciale)\n"
                "\u2022 \U0001f4c8 Acces au classement et au panel cuisto prive\n\n"
                "\u26a0\ufe0f **Requis :**\n"
                "\u2022 Detenteur de la **Tech Uber** obligatoire\n"
                "\u2022 Si tu ne l'as pas : <#1514065238243414066>\n\n"
                "\U0001f4b0 **Infos importantes :**\n"
                "\u2022 Les **frais de carte ou de compte** restent a **ta charge**\n"
                "\u2022 Les **codes promo** sont selon **ton grade** (plus ton grade est eleve, meilleure est la reduction)\n"
                "\u2022 C'est **toi qui geres le retrait de ton argent**\n\n"
                f"\U0001f447 Semaine **{week_count}** -> {next_level}"
            ),
            color=CUISTO_COLOR,
        )
        embed.add_field(name="Client", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        embed.add_field(name="Statut", value="En attente de paiement", inline=False)

        founder_ids = getattr(bot.settings, 'founder_role_ids', ())
        founder_role = interaction.guild.get_role(founder_ids[0]) if founder_ids else None
        mention = founder_role.mention if founder_role else "@Fondateur"
        await channel.send(mention)
        await channel.send(embed=embed, view=CuistoTicketView(price=price, week_number=week_count))
        await channel.send(f"{interaction.user.mention} ton ticket cuisto est ouvert ! Un Fondateur ou Chef Cuisto va s'occuper de toi.")
        await interaction.response.send_message(f"\u2705 Ticket cree : {channel.mention}", ephemeral=True)


class CuistoTicketView(discord.ui.View):
    def __init__(self, price: float, week_number: int) -> None:
        super().__init__(timeout=None)
        self.price = price
        self.week_number = week_number

    @discord.ui.button(label="PayPal", style=discord.ButtonStyle.secondary, emoji="\U0001f4b5", custom_id="ez:cuisto:ticket:paypal")
    async def paypal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        await self._send_payment(interaction, "PayPal", bot.settings.paypal_link, bot.settings.paypal_text, 0x1ABC9C)

    @discord.ui.button(label="Revolut", style=discord.ButtonStyle.secondary, emoji="\U0001f4b3", custom_id="ez:cuisto:ticket:revolut")
    async def revolut(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        await self._send_payment(interaction, "Revolut", bot.settings.revolut_link, bot.settings.revolut_text, 0x9B59B6)

    @discord.ui.button(label="Paysafecard", style=discord.ButtonStyle.secondary, emoji="\U0001f4b0", custom_id="ez:cuisto:ticket:paysafe")
    async def paysafe(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not _is_staff(bot, interaction):
            return
        await interaction.response.send_modal(CuistoPaysafeModal(self.price, self.week_number))

    @discord.ui.button(label="Crypto (auto)", style=discord.ButtonStyle.secondary, emoji="\U0001fa99", custom_id="ez:cuisto:ticket:crypto")
    async def crypto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not _is_staff(bot, interaction):
            return
        try:
            payment_url, external_id = await create_cuisto_oxapay_invoice(bot.settings, self.price, interaction.user.id)
        except RuntimeError as error:
            await send_ephemeral(interaction, f"\u274c {error}")
            return
        embed = discord.Embed(title="Paiement Crypto (auto) - Abonnement Cuisto", color=0xF39C12)
        embed.description = (
            f"Montant a payer : **{self.price:.2f} EUR**\n"
            f"Cryptos acceptees : `{', '.join(bot.settings.oxapay_allowed_coins)}`\n"
            f"Expiration : **{bot.settings.oxapay_lifetime_minutes} min**\n"
            f"[Cliquer ici pour payer]({payment_url})"
        )
        await interaction.response.send_message(embed=embed)
        if external_id:
            bot.db.create_payment(
                ticket_id=0, channel_id=interaction.channel.id or 0,
                kind="crypto", provider="oxapay", amount=self.price,
                currency=bot.settings.oxapay_currency, status="pending",
                payment_url=payment_url, external_id=external_id,
                created_by=interaction.user.id,
            )

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.success, emoji="\u2705", custom_id="ez:cuisto:ticket:confirm")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not _is_staff(bot, interaction):
            return
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await send_ephemeral(interaction, "Ticket introuvable.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _apply_cuisto_role(bot, interaction, ticket, self.price, "manuel", self.week_number)
        await interaction.followup.send("\u2705 Paiement confirme, role attribue !", ephemeral=True)

    @discord.ui.button(label="Refuser", style=discord.ButtonStyle.danger, emoji="\u274c", custom_id="ez:cuisto:ticket:refuse")
    async def refuse(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not _is_staff(bot, interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.channel.send(f"\u274c Paiement refuse par {interaction.user.mention}.")
        bot.db.close_ticket(
            int(bot.db.ticket_by_channel(interaction.channel.id)["id"]),
            transcript_path=None, order_cost=0, resale_amount=0, profit_amount=0, salary_amount=0,
        )
        await interaction.followup.send("Ticket ferme.", ephemeral=True)
        try:
            await interaction.channel.delete(reason="Paiement cuisto refuse")
        except discord.DiscordException:
            pass

    async def _send_payment(self, interaction: discord.Interaction, label: str, link: str, text: str, color: int) -> None:
        bot: commands.Bot = interaction.client
        if not _is_staff(bot, interaction):
            return
        embed = discord.Embed(title=f"Paiement {label} - Abonnement Cuisto", color=color)
        embed.description = (
            f"Montant a payer : **{self.price:.2f} EUR**\n\n"
            f"{text}\n\n"
            f"Lien : {link}\n\n"
            "\U0001f4f8 Apres paiement, envoie une capture d'ecran comme preuve."
        )
        await interaction.response.send_message(embed=embed)


class CuistoPaysafeModal(discord.ui.Modal, title="Code Paysafecard"):
    code = discord.ui.TextInput(
        label="Code Paysafecard",
        placeholder="Entre le code paysafe recu du client",
        max_length=100,
    )

    def __init__(self, price: float, week_number: int) -> None:
        super().__init__()
        self.price = price
        self.week_number = week_number

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_cuisto_staff(bot, interaction.user):
            await interaction.response.send_message("\u274c Staff uniquement.", ephemeral=True)
            return
        code_value = str(self.code).strip()
        if not code_value:
            await interaction.response.send_message("\u274c Code requis.", ephemeral=True)
            return
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("Ticket introuvable.", ephemeral=True)
            return
        embed = discord.Embed(title="Paiement Paysafecard - Abonnement Cuisto", color=0x3498DB)
        embed.description = (
            f"Montant a payer : **{self.price:.2f} EUR**\n\n"
            f"Code Paysafecard recu :\n"
            f"```{code_value}```\n\n"
            f"Client: <@{ticket['creator_id']}>\n"
            f"Valide le paiement avec Confirmer ou refuse avec Refuser."
        )
        await interaction.response.send_message(embed=embed)
        bot.db.create_payment(
            ticket_id=ticket["id"], channel_id=interaction.channel.id or 0,
            kind="paysafe", provider="manual", amount=self.price,
            currency="EUR", status="pending",
            payment_url=bot.settings.paysafe_link, external_id=code_value,
            created_by=interaction.user.id,
        )


def _is_staff(bot, interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False
    if not is_cuisto_staff(bot, interaction.user):
        asyncio.create_task(send_ephemeral(interaction, "\u274c Staff uniquement."))
        return False
    return True


async def _apply_cuisto_role(bot, interaction: discord.Interaction, ticket, price: float, method: str, week_number: int) -> None:
    user_id = int(ticket["creator_id"])
    user_name = ticket["creator_name"]

    record_cuisto_payment(
        bot.db,
        user_id=user_id,
        user_name=user_name,
        amount=price,
        payment_method=method,
        week_number=week_number,
    )

    week_count = get_user_payment_count(bot.db, user_id)
    guild = interaction.guild
    if not guild:
        return

    member = guild.get_member(user_id)
    if not member:
        return

    cuisto_role = guild.get_role(CUISTO_ROLE_ID)
    if cuisto_role and cuisto_role not in member.roles:
        try:
            await member.add_roles(cuisto_role, reason="Abonnement cuisto - role Cuisto")
        except discord.DiscordException:
            pass

    apprenti_role = guild.get_role(APPRENTI_ROLE_ID)

    if week_count >= WEEKS_TO_MASTER:
        maitre_role_id = bot.settings.maitre_cuisto_role_id
        maitre_role = guild.get_role(maitre_role_id) if maitre_role_id else None
        if maitre_role and maitre_role not in member.roles and maitre_role.id != CUISTO_ROLE_ID:
            try:
                await member.add_roles(maitre_role, reason="Abonnement cuisto - passage Maitre")
            except discord.DiscordException:
                pass
        if apprenti_role and apprenti_role in member.roles:
            try:
                await member.remove_roles(apprenti_role, reason="Promu Maitre Cuisto")
            except discord.DiscordException:
                pass
        level_text = "\U0001f451 **Maitre Cuisto** - Felicitations !"
    else:
        if apprenti_role and apprenti_role not in member.roles and apprenti_role.id != CUISTO_ROLE_ID:
            try:
                await member.add_roles(apprenti_role, reason="Abonnement cuisto - Apprenti")
            except discord.DiscordException:
                pass
        level_text = f"\U0001f4aa **Apprenti** (Semaine {week_count}/{WEEKS_TO_MASTER})"

    bot.db.close_ticket(
        int(ticket["id"]),
        transcript_path=None, order_cost=0, resale_amount=0, profit_amount=0, salary_amount=0,
    )

    await interaction.channel.send(
        f"\u2705 {member.mention} a paye son abonnement cuisto ({price:.2f} EUR - {method.upper()}) !\n"
        f"{level_text}\n"
        f"Prochain paiement dans **7 jours** pour conserver le role."
    )

    try:
        await interaction.channel.delete(reason="Ticket cuisto ferme")
    except discord.DiscordException:
        pass


async def create_cuisto_oxapay_invoice(settings, amount: float, user_id: int) -> tuple[str, str | None]:
    if not settings.oxapay_api_key:
        raise RuntimeError("Cle OxaPay manquante.")
    payload: dict = {
        "amount": amount,
        "currency": settings.oxapay_currency,
        "lifetime": settings.oxapay_lifetime_minutes,
        "order_id": f"cuisto-{user_id}-{int(datetime.now().timestamp())}",
        "description": f"Abonnement cuisto #{user_id}",
        "fee_paid_by_payer": 1,
        "mixed_payment": False,
        "sandbox": False,
    }
    headers = {"merchant_api_key": settings.oxapay_api_key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(settings.oxapay_invoice_url, json=payload, headers=headers)
        body_preview = response.text[:300]
        if response.status_code == 401:
            raise RuntimeError("OxaPay dit que la Merchant API Key est invalide.")
        if response.status_code == 403:
            raise RuntimeError("OxaPay bloque la requete. Verifie les restrictions IP.")
        if response.status_code >= 400:
            raise RuntimeError(f"OxaPay refuse ({response.status_code}). {body_preview}")
        data = response.json()
        data_block = data.get("data") if isinstance(data.get("data"), dict) else {}
        payment_url = (
            data_block.get("payment_url") or data_block.get("paymentUrl")
            or data_block.get("payLink") or data.get("payment_url")
            or data.get("paymentUrl") or data.get("payLink") or data.get("url")
        )
        external_id = data_block.get("track_id") or data_block.get("trackId") or data.get("track_id") or data.get("trackId")
        if not payment_url:
            raise RuntimeError(f"Pas de lien de paiement. {body_preview}")
        return str(payment_url), str(external_id) if external_id else None


class CuistoPriceModal(discord.ui.Modal, title="Prix abonnement Cuisto"):
    dispo_price = discord.ui.TextInput(label="Prix DISPO (EUR)", default="15.00", max_length=10)
    attente_price = discord.ui.TextInput(label="Prix ATTENTE (EUR)", default="20.00", max_length=10)
    off_price = discord.ui.TextInput(label="Prix OFF (0 = ferme)", default="0", max_length=10)

    def __init__(self, current_prices: dict) -> None:
        super().__init__()
        self.dispo_price.default = f"{current_prices['dispo']:.2f}"
        self.attente_price.default = f"{current_prices['attente']:.2f}"
        self.off_price.default = f"{current_prices['off']:.0f}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        try:
            dispo = float(str(self.dispo_price).replace(",", "."))
            attente = float(str(self.attente_price).replace(",", "."))
            off = float(str(self.off_price).replace(",", "."))
        except ValueError:
            await interaction.response.send_message("\u274c Prix invalide.", ephemeral=True)
            return
        update_cuisto_prices(bot.db, dispo, attente, off)
        await interaction.response.send_message(
            f"\u2705 Prix mis a jour : DISPO={dispo:.2f}EUR | ATTENTE={attente:.2f}EUR | OFF={'Ferme' if off <= 0 else f'{off:.2f}EUR'}",
            ephemeral=True,
        )


class CuistoAdminView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Configurer les prix", style=discord.ButtonStyle.primary, emoji="\U0001f4b0", custom_id="ez:cuisto:admin:price")
    async def config_price(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await send_ephemeral(interaction, "\u274c Admin uniquement.")
            return
        prices = get_cuisto_prices(bot.db)
        await interaction.response.send_modal(CuistoPriceModal(prices))

    @discord.ui.button(label="Voir les abonnes", style=discord.ButtonStyle.secondary, emoji="\U0001f4cb", custom_id="ez:cuisto:admin:list")
    async def list_subs(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await send_ephemeral(interaction, "\u274c Admin uniquement.")
            return
        rows = bot.db.conn.execute(
            "SELECT * FROM cuisto_subscriptions WHERE active = 1 ORDER BY last_payment_at DESC LIMIT 20"
        ).fetchall()
        if not rows:
            await send_ephemeral(interaction, "Aucun abonne pour le moment.")
            return
        lines = ["**\U0001f468\u200d\U0001f373 Abonnes Cuisto :**"]
        for r in rows:
            level = "\U0001f451 Maitre" if r["role_level"] == "master" else "\U0001f4aa Apprenti"
            week = f"S{r['current_week']}/{WEEKS_TO_MASTER}"
            lines.append(f"{level} <@{r['user_id']}> | {week} | Dernier: {r['last_payment_at'][:10] if r['last_payment_at'] else 'N/A'}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.success, emoji="\U0001f4ca", custom_id="ez:cuisto:admin:stats")
    async def stats(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await send_ephemeral(interaction, "\u274c Admin uniquement.")
            return
        total = bot.db.conn.execute("SELECT COUNT(*) FROM cuisto_subscriptions WHERE active = 1").fetchone()[0]
        apprentices = bot.db.conn.execute("SELECT COUNT(*) FROM cuisto_subscriptions WHERE active = 1 AND role_level = 'apprentice'").fetchone()[0]
        masters = bot.db.conn.execute("SELECT COUNT(*) FROM cuisto_subscriptions WHERE active = 1 AND role_level = 'master'").fetchone()[0]
        revenue = bot.db.conn.execute("SELECT COALESCE(SUM(amount), 0) FROM cuisto_payments WHERE status = 'paid'").fetchone()[0]
        this_week = bot.db.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cuisto_payments WHERE status = 'paid' AND created_at >= datetime('now', '-7 days')"
        ).fetchone()[0]
        embed = discord.Embed(title="\U0001f4ca Stats Abonnement Cuisto", color=CUISTO_COLOR)
        embed.add_field(name="Abonnes actifs", value=str(total), inline=True)
        embed.add_field(name="Apprentis", value=str(apprentices), inline=True)
        embed.add_field(name="Maitres", value=str(masters), inline=True)
        embed.add_field(name="Revenu total", value=f"{revenue:.2f} EUR", inline=True)
        embed.add_field(name="Cette semaine", value=f"{this_week:.2f} EUR", inline=True)
        prices = get_cuisto_prices(bot.db)
        embed.add_field(name="Prix DISPO/ATTENTE", value=f"{prices['dispo']:.2f}/{prices['attente']:.2f} EUR", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
