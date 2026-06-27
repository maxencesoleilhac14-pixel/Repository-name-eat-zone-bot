from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import string
from datetime import datetime, timedelta, timezone

import discord
import httpx
from discord.ext import commands

SELLER_ROLE_ID = 1514068068320411658
BASICFIT_COLOR = 0xFF8C00

PANEL_DESCRIPTION = (
    "**Basic-Fit Ultimate** - L'abonnement le plus complet !\n\n"
    ":weight_lifter: Acces 24h/24 et 7j/7 a tous les clubs\n"
    ":busts_in_silhouette: Invite un(e) ami(e) a chaque seance\n"
    ":droplet: YANGA Sports Water inclus\n"
    ":massage: Acces illimite aux fauteuils massants\n"
    ":snowflake: Gele ton abonnement sans frais (2x/an)\n"
    ":earth_africa: Acces a tous les clubs en Europe\n"
    ":iphone: App avec 1000+ entrainements\n\n"
    ":heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign:\n"
    "**Offres speciales :**\n"
    ":orange_circle: **Mensuel** - **11 EUR**\n"
    ":orange_circle: **Annuel** - **112 EUR**\n"
    ":heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign::heavy_minus_sign:\n"
    ":key: Tu as deja un compte ? Connecte-toi !"
)


def init_basicfit_tables(db) -> None:
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS basicfit_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            plan_type TEXT NOT NULL,
            buyer_id INTEGER NOT NULL,
            buyer_name TEXT NOT NULL,
            purchased_at TEXT NOT NULL,
            expires_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            last_request_at TEXT
        );

        CREATE TABLE IF NOT EXISTS basicfit_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            password TEXT,
            plan_type TEXT NOT NULL,
            price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            ticket_channel_id INTEGER,
            client_info TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
    """)
    db.conn.commit()


def generate_bf_username() -> str:
    return "BF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_bf_password() -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=14))


async def send_ephemeral(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def find_or_create_bf_category(guild: discord.Guild, name: str, candidates: tuple[str, ...]) -> discord.CategoryChannel:
    normalized = {c.lower().replace(" ", "").replace("-", "") for c in candidates}
    for cat in guild.categories:
        n = cat.name.lower().replace(" ", "").replace("-", "")
        if n in normalized:
            return cat
        if any(c in n for c in normalized):
            return cat
    return await guild.create_category(name, reason="Categorie Basic-Fit")


async def make_bf_overwrites(bot, guild: discord.Guild, user: discord.Member) -> dict:
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
    }
    seller_role = guild.get_role(SELLER_ROLE_ID)
    if seller_role:
        overwrites[seller_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
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


def is_bf_staff(bot, member: discord.Member) -> bool:
    seller_role = member.guild.get_role(SELLER_ROLE_ID)
    if seller_role and seller_role in member.roles:
        return True
    if member.guild_permissions.administrator:
        return True
    for role_id in bot.settings.admin_role_ids:
        if member.get_role(role_id):
            return True
    founder_ids = getattr(bot.settings, 'founder_role_ids', ())
    for role_id in founder_ids:
        if member.get_role(role_id):
            return True
    return False


class BasicFitPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Connexion", style=discord.ButtonStyle.secondary, emoji="\U0001f511", custom_id="ez:bf:login")
    async def connexion(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BasicFitLoginModal())

    @discord.ui.button(label="Acheter", style=discord.ButtonStyle.success, emoji="\U0001f4b3", custom_id="ez:bf:buy")
    async def acheter(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        view = BasicFitPlanView()
        embed = discord.Embed(
            title="Basic-Fit Ultimate | Choix de l'offre",
            description="Choisis ton offre ci-dessous :",
            color=BASICFIT_COLOR,
        )
        embed.add_field(name="Mensuel - 11EUR", value="Paiement unique, acces 1 mois", inline=True)
        embed.add_field(name="Annuel - 112EUR", value="Paiement unique, acces 1 an", inline=True)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class BasicFitPlanView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)

    @discord.ui.button(label="Mensuel - 11EUR", style=discord.ButtonStyle.primary, emoji="\U0001f4c5", custom_id="ez:bf:plan:month")
    async def mensuel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BasicFitBuyModal("monthly", 11.0))

    @discord.ui.button(label="Annuel - 112EUR", style=discord.ButtonStyle.success, emoji="\U0001f31f", custom_id="ez:bf:plan:year")
    async def annuel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(BasicFitBuyModal("yearly", 112.0))


class BasicFitBuyModal(discord.ui.Modal):
    def __init__(self, plan_type: str, price: float) -> None:
        title = "Achat Mensuel" if plan_type == "monthly" else "Achat Annuel"
        super().__init__(title=title)
        self.plan_type = plan_type
        self.price = price
        self.username_input = discord.ui.TextInput(
            label="Nom d'utilisateur (vide = aleatoire)",
            placeholder="Ex: JeanDupont ou laisser vide",
            required=False,
            max_length=30,
        )
        self.nom_prenom = discord.ui.TextInput(
            label="Nom & Prenom (vide = aleatoire)",
            placeholder="Ex: Dupont Jean ou laisser vide",
            required=False,
            max_length=60,
        )
        self.adresse_cp = discord.ui.TextInput(
            label="Adresse & Code Postal (vide = aleatoire)",
            placeholder="Ex: 12 rue de la Paix, 75001 Paris",
            required=False,
            max_length=120,
        )
        self.email = discord.ui.TextInput(
            label="Email (vide = aleatoire)",
            placeholder="Ex: jean@email.com ou laisser vide",
            required=False,
            max_length=80,
        )
        self.tel_ddn = discord.ui.TextInput(
            label="Tel & Date naissance (vide = aleatoire)",
            placeholder="Ex: 0612345678, 01/01/2000",
            required=False,
            max_length=60,
        )
        self.add_item(self.username_input)
        self.add_item(self.nom_prenom)
        self.add_item(self.adresse_cp)
        self.add_item(self.email)
        self.add_item(self.tel_ddn)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        db = bot.db

        chosen = str(self.username_input).strip()
        if chosen:
            existing = db.conn.execute(
                "SELECT id FROM basicfit_accounts WHERE username = ?", (chosen,)
            ).fetchone()
            if existing:
                await interaction.response.send_message("Nom d'utilisateur deja pris.", ephemeral=True)
                return

        info = {
            "nom_prenom": str(self.nom_prenom).strip(),
            "adresse_cp": str(self.adresse_cp).strip(),
            "email": str(self.email).strip(),
            "tel_ddn": str(self.tel_ddn).strip(),
        }
        has_info = any(v for v in info.values())
        info_json = json.dumps(info, ensure_ascii=False)

        label = "Mensuel" if self.plan_type == "monthly" else "Annuel"
        ticket_id = db.create_ticket(
            guild_id=interaction.guild.id,
            creator_id=interaction.user.id,
            creator_name=str(interaction.user),
            ticket_type="basicfit",
            address=f"Basic-Fit {label}",
            payment_enabled=True,
        )

        category = await find_or_create_bf_category(
            interaction.guild,
            "Basic-Fit Achats",
            ("basicfit achat", "basicfit", "bf achat", "basic-fit achat"),
        )
        overwrites = await make_bf_overwrites(bot, interaction.guild, interaction.user)
        channel = await interaction.guild.create_text_channel(
            name=f"bf-achat-{ticket_id:04d}",
            category=category,
            overwrites=overwrites,
            reason=f"Ticket Basic-Fit #{ticket_id}",
        )
        db.attach_channel(ticket_id, channel.id)

        now = datetime.now(timezone.utc).isoformat()
        uname = chosen if chosen else None
        db.conn.execute(
            "INSERT INTO basicfit_purchases (user_id, username, password, plan_type, price, status, ticket_channel_id, client_info, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (interaction.user.id, uname, None, self.plan_type, self.price, channel.id, info_json, now),
        )
        db.conn.commit()
        purchase_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        embed = discord.Embed(title=f"Basic-Fit Ultimate | {label} #{ticket_id}", color=BASICFIT_COLOR)
        embed.add_field(name="Client", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        embed.add_field(name="Offre", value=f"**{label}**", inline=True)
        embed.add_field(name="Prix", value=f"**{self.price:.2f} EUR**", inline=True)
        if chosen:
            embed.add_field(name="Nom d'utilisateur", value=f"`{chosen}`", inline=False)
        else:
            embed.add_field(name="Identifiants", value="Generes apres paiement", inline=False)
        if has_info:
            parts = []
            if info["nom_prenom"]:
                parts.append(f"**Nom & Prenom :** {info['nom_prenom']}")
            if info["adresse_cp"]:
                parts.append(f"**Adresse :** {info['adresse_cp']}")
            if info["email"]:
                parts.append(f"**Email :** {info['email']}")
            if info["tel_ddn"]:
                parts.append(f"**Tel/DDN :** {info['tel_ddn']}")
            embed.add_field(name="Infos client", value="\n".join(parts), inline=False)
        else:
            embed.add_field(name="Infos client", value="Compte aleatoire (aucune info fournie)", inline=False)
        embed.add_field(name="Statut", value="En attente de paiement", inline=False)

        seller_role = interaction.guild.get_role(SELLER_ROLE_ID)
        mention = f"<@&{SELLER_ROLE_ID}>" if seller_role else "@vendeur"
        await channel.send(mention)
        await channel.send(embed=embed, view=BasicFitTicketView())
        await channel.send(f"{interaction.user.mention} ton ticket Basic-Fit est ouvert ! Un vendeur va s'occuper de toi.")
        await interaction.response.send_message(f"Ticket cree : {channel.mention}", ephemeral=True)


class BasicFitTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="PayPal", style=discord.ButtonStyle.secondary, emoji="\U0001f4b5", custom_id="ez:bf:ticket:paypal")
    async def paypal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.send_modal(BFPaymentModal("paypal"))

    @discord.ui.button(label="Revolut", style=discord.ButtonStyle.secondary, emoji="\U0001f4b3", custom_id="ez:bf:ticket:revolut")
    async def revolut(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.send_modal(BFPaymentModal("revolut"))

    @discord.ui.button(label="Crypto", style=discord.ButtonStyle.secondary, emoji="\U0001fa99", custom_id="ez:bf:ticket:crypto")
    async def crypto(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.send_modal(BFPaymentModal("crypto"))

    @discord.ui.button(label="Paysafe", style=discord.ButtonStyle.secondary, emoji="\U0001f4b0", custom_id="ez:bf:ticket:paysafe")
    async def paysafe(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.send_modal(BFPaymentModal("paysafe"))

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.success, emoji="\u2705", custom_id="ez:bf:ticket:confirm")
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("Ticket introuvable.", ephemeral=True)
            return
        await interaction.response.send_modal(BFConfirmEmailModal())

    @discord.ui.button(label="Fermer", style=discord.ButtonStyle.danger, emoji="\U0001f512", custom_id="ez:bf:ticket:close")
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.followup.send("Ticket introuvable.", ephemeral=True)
            return
        bot.db.close_ticket(
            int(ticket["id"]),
            transcript_path=None,
            order_cost=0,
            resale_amount=0,
            profit_amount=0,
            salary_amount=0,
        )
        await interaction.followup.send("Ticket ferme.", ephemeral=True)
        try:
            await interaction.channel.delete(reason="Ticket Basic-Fit ferme")
        except discord.DiscordException:
            pass


class BFPaymentModal(discord.ui.Modal):
    def __init__(self, kind: str) -> None:
        titles = {"paypal": "Paiement PayPal", "revolut": "Paiement Revolut", "crypto": "Paiement Crypto", "paysafe": "Paiement Paysafecard"}
        super().__init__(title=titles.get(kind, "Paiement"))
        self.kind = kind
        self.amount = discord.ui.TextInput(label="Montant a payer", placeholder="Ex: 11.00", max_length=20)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Utilisable uniquement dans un ticket.", ephemeral=True)
            return
        purchase = bot.db.conn.execute(
            "SELECT * FROM basicfit_purchases WHERE ticket_channel_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (interaction.channel.id,),
        ).fetchone()
        if not purchase:
            await interaction.response.send_message("Aucun achat en attente.", ephemeral=True)
            return

        try:
            cleaned = str(self.amount).strip().replace("EUR", "").replace("eur", "").replace(",", ".")
            amount = round(float(cleaned), 2)
        except ValueError:
            await interaction.response.send_message("Montant invalide.", ephemeral=True)
            return

        if self.kind == "paypal":
            embed = discord.Embed(title="Paiement PayPal - Basic-Fit", color=0x1ABC9C)
            embed.description = (
                f"Montant a payer : **{amount:.2f} EUR**\n\n"
                f"{bot.settings.paypal_text}\n\n"
                f"Lien : {bot.settings.paypal_link}"
            )
            bot.db.create_payment(
                ticket_id=purchase["id"],
                channel_id=interaction.channel.id,
                kind="paypal",
                provider="manual",
                amount=amount,
                currency="EUR",
                status="pending",
                payment_url=bot.settings.paypal_link,
                external_id=None,
                created_by=interaction.user.id,
            )
            await interaction.response.send_message("PayPal envoye.", ephemeral=True)
            await interaction.channel.send(embed=embed)
            return

        if self.kind == "revolut":
            embed = discord.Embed(title="Paiement Revolut - Basic-Fit", color=0x9B59B6)
            embed.description = (
                f"Montant a payer : **{amount:.2f} EUR**\n\n"
                f"{bot.settings.revolut_text}\n\n"
                f"Lien : {bot.settings.revolut_link}"
            )
            bot.db.create_payment(
                ticket_id=purchase["id"],
                channel_id=interaction.channel.id,
                kind="revolut",
                provider="manual",
                amount=amount,
                currency="EUR",
                status="pending",
                payment_url=bot.settings.revolut_link,
                external_id=None,
                created_by=interaction.user.id,
            )
            await interaction.response.send_message("Revolut envoye.", ephemeral=True)
            await interaction.channel.send(embed=embed)
            return

        if self.kind == "paysafe":
            embed = discord.Embed(title="Paiement Paysafe - Basic-Fit", color=0x3498DB)
            embed.description = (
                f"Montant a payer : **{amount:.2f} EUR**\n\n"
                f"{bot.settings.paysafe_text}\n\n"
                f"Lien : {bot.settings.paysafe_link}"
            )
            bot.db.create_payment(
                ticket_id=purchase["id"],
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
            await interaction.response.send_message("Paysafe envoye.", ephemeral=True)
            await interaction.channel.send(embed=embed)
            return

        try:
            payment_url, external_id = await create_bf_oxapay_invoice(bot.settings, amount, purchase["id"])
        except RuntimeError as error:
            await interaction.response.send_message(f"{error}", ephemeral=True)
            return
        bot.db.create_payment(
            ticket_id=purchase["id"],
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
        embed = discord.Embed(title="Paiement Crypto - Basic-Fit", color=0xF39C12)
        embed.description = (
            f"Montant a payer : **{amount:.2f} EUR**\n"
            f"Cryptos acceptees : `{', '.join(bot.settings.oxapay_allowed_coins)}`\n"
            f"Expiration : **{bot.settings.oxapay_lifetime_minutes} min**\n\n"
            f"[Cliquer ici pour payer]({payment_url})"
        )
        await interaction.response.send_message("Lien crypto envoye.", ephemeral=True)
        await interaction.channel.send(embed=embed)


async def create_bf_oxapay_invoice(settings, amount: float, purchase_id: int) -> tuple[str, str | None]:
    if not settings.oxapay_api_key:
        raise RuntimeError("Cle OxaPay manquante.")
    payload: dict = {
        "amount": amount,
        "currency": settings.oxapay_currency,
        "lifetime": settings.oxapay_lifetime_minutes,
        "order_id": f"bf-{purchase_id}-{int(datetime.now().timestamp())}",
        "description": f"Basic-Fit achat #{purchase_id}",
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


class BFConfirmEmailModal(discord.ui.Modal, title="Confirmer paiement Basic-Fit"):
    email = discord.ui.TextInput(label="Email du compte Basic-Fit", placeholder="Ex: client@email.com", max_length=100)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("Ticket introuvable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await handle_basicfit_confirm(bot, interaction, ticket, str(self.email).strip())


class BasicFitLoginModal(discord.ui.Modal, title="Connexion Basic-Fit"):
    username = discord.ui.TextInput(label="Nom d'utilisateur", placeholder="Ton identifiant", max_length=30)
    password = discord.ui.TextInput(label="Mot de passe", placeholder="Ton mot de passe", max_length=50)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        db = bot.db

        uname = str(self.username).strip()
        pwd = str(self.password).strip()

        account = db.conn.execute(
            "SELECT * FROM basicfit_accounts WHERE username = ? AND password = ? AND active = 1",
            (uname, pwd),
        ).fetchone()

        if not account:
            await interaction.response.send_message("Identifiants invalides ou compte inactif.", ephemeral=True)
            return

        plan_label = "Mensuel" if account["plan_type"] == "monthly" else "Annuel"
        embed = discord.Embed(title="Connecte a Basic-Fit", color=BASICFIT_COLOR)
        embed.add_field(name="Utilisateur", value=f"`{account['username']}`", inline=True)
        embed.add_field(name="Offre", value=f"**{plan_label}**", inline=True)
        if account["expires_at"]:
            embed.add_field(name="Expire le", value=account["expires_at"], inline=True)
        embed.description = "Tu peux demander un compte via le bouton ci-dessous."

        view = BasicFitConnectedView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class BasicFitConnectedView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=120)

    @discord.ui.button(label="Demander un compte", style=discord.ButtonStyle.primary, emoji="\U0001f4dd", custom_id="ez:bf:request")
    async def request_account(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        db = bot.db

        if not interaction.guild:
            return

        account = db.conn.execute(
            "SELECT * FROM basicfit_accounts WHERE buyer_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
            (interaction.user.id,),
        ).fetchone()

        if not account:
            await interaction.response.send_message("Aucun abonnement actif trouve.", ephemeral=True)
            return

        now = datetime.now(timezone.utc)
        if account["expires_at"]:
            exp = datetime.fromisoformat(account["expires_at"])
            if now > exp:
                await interaction.response.send_message("Ton abonnement a expire. Tu n'as plus droit aux demandes de compte.", ephemeral=True)
                return

        if account["last_request_at"]:
            last = datetime.fromisoformat(account["last_request_at"])
            if last.month == now.month and last.year == now.year:
                await interaction.response.send_message(
                    "Tu as deja fait une demande ce mois-ci. Reviens le mois prochain !",
                    ephemeral=True,
                )
                return

        db.conn.execute(
            "UPDATE basicfit_accounts SET last_request_at = ? WHERE id = ?",
            (now.isoformat(), account["id"]),
        )
        db.conn.commit()

        ticket_id = db.create_ticket(
            guild_id=interaction.guild.id,
            creator_id=interaction.user.id,
            creator_name=str(interaction.user),
            ticket_type="basicfit_request",
            address="Demande de compte Basic-Fit",
            payment_enabled=False,
        )

        category = await find_or_create_bf_category(
            interaction.guild,
            "Basic-Fit Demandes",
            ("basicfit demande", "bf demande", "basic-fit demande"),
        )
        overwrites = await make_bf_overwrites(bot, interaction.guild, interaction.user)
        channel = await interaction.guild.create_text_channel(
            name=f"bf-dmd-{ticket_id:04d}",
            category=category,
            overwrites=overwrites,
            reason=f"Demande compte Basic-Fit #{ticket_id}",
        )
        db.attach_channel(ticket_id, channel.id)

        embed = discord.Embed(title=f"Demande de compte Basic-Fit #{ticket_id}", color=BASICFIT_COLOR)
        embed.add_field(name="Client", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=False)
        embed.add_field(name="Type", value="Demande de compte", inline=False)

        seller_role = interaction.guild.get_role(SELLER_ROLE_ID)
        mention = f"<@&{SELLER_ROLE_ID}>" if seller_role else "@vendeur"
        await channel.send(mention)
        await channel.send(embed=embed, view=BasicFitSendAccountView())
        await interaction.response.send_message(f"Demande envoyee : {channel.mention}", ephemeral=True)


class BasicFitSendAccountView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Envoyer le compte", style=discord.ButtonStyle.success, emoji="\U0001f4e6", custom_id="ez:bf:sendaccount")
    async def send_account(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        ticket = bot.db.ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("Ticket introuvable.", ephemeral=True)
            return
        await interaction.response.send_modal(BFSendAccountModal(ticket))


class BFSendAccountModal(discord.ui.Modal, title="Envoyer le compte Basic-Fit"):
    email = discord.ui.TextInput(label="Email du compte Basic-Fit", placeholder="Ex: client@email.com", max_length=100)

    def __init__(self, ticket) -> None:
        super().__init__()
        self.ticket = ticket

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        bf_email = str(self.email).strip()
        if not bf_email:
            await interaction.response.send_message("Email requis.", ephemeral=True)
            return

        account = bot.db.conn.execute(
            "SELECT * FROM basicfit_accounts WHERE buyer_id = ? AND active = 1 ORDER BY id DESC LIMIT 1",
            (self.ticket["creator_id"],),
        ).fetchone()

        if not account:
            await interaction.response.send_message("Aucun compte actif trouve pour cet abonne.", ephemeral=True)
            return

        identifiant = account["username"]
        mdp = account["password"]

        instructions = (
            ">>> :warning: **Procedure :**\n\n"
            "1. Va sur l'application/site **Basic-Fit**\n"
            "2. Clique sur **Mot de passe oublie**\n"
            "3. Fais la demande **directement**\n"
            "4. **Le mot de passe ne change pas**, c'est toujours le meme\n\n"
            ":fire: **Fais-le tout de suite !** Les emails sont temporaires."
        )

        embed = discord.Embed(title="Compte Basic-Fit envoye", color=discord.Color.green())
        embed.add_field(name="Identifiant", value=f"**`{identifiant}`**", inline=False)
        embed.add_field(name="Mot de passe", value=f"**`{mdp}`**", inline=False)
        embed.add_field(name="Email Basic-Fit", value=f"`{bf_email}`", inline=False)
        embed.add_field(name="Instructions", value=instructions, inline=False)
        embed.set_footer(text="Le mot de passe ne change pas.")
        await interaction.response.send_message(embed=embed, ephemeral=False)

        try:
            user = bot.get_user(int(self.ticket["creator_id"])) or await bot.fetch_user(int(self.ticket["creator_id"]))
            dm = discord.Embed(title="Ton compte Basic-Fit", color=BASICFIT_COLOR)
            dm.add_field(name="Identifiant", value=f"**`{identifiant}`**", inline=False)
            dm.add_field(name="Mot de passe", value=f"**`{mdp}`**", inline=False)
            dm.add_field(name="Email Basic-Fit", value=f"`{bf_email}`", inline=False)
            dm.add_field(
                name="Mot de passe oublie Basic-Fit",
                value=(
                    "Va sur l'app Basic-Fit > Mot de passe oublie\n"
                    "Fais la demande immediatement.\n"
                    "Le mot de passe reste le meme.\n\n"
                    ":fire: Fais-le vite, les emails sont temporaires !"
                ),
                inline=False,
            )
            dm.set_footer(text="Le mot de passe ne change pas.")
            await user.send(embed=dm)
            await interaction.channel.send(f"Compte envoye en MP a <@{self.ticket['creator_id']}>.")
        except discord.DiscordException:
            await interaction.channel.send(f"Impossible d'envoyer un MP a <@{self.ticket['creator_id']}>.")


async def handle_basicfit_confirm(bot, interaction: discord.Interaction, ticket, bf_email: str = "") -> None:
    db = bot.db

    purchase = db.conn.execute(
        "SELECT * FROM basicfit_purchases WHERE ticket_channel_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (interaction.channel.id,),
    ).fetchone()

    if not purchase:
        await interaction.channel.send("Aucun achat Basic-Fit en attente trouve pour ce ticket.")
        return

    username = purchase["username"]
    password = purchase["password"]

    if not username or not password:
        username = generate_bf_username()
        password = generate_bf_password()
        while db.conn.execute("SELECT id FROM basicfit_accounts WHERE username = ?", (username,)).fetchone():
            username = generate_bf_username()
            password = generate_bf_password()

    plan_label = "Mensuel" if purchase["plan_type"] == "monthly" else "Annuel"
    now = datetime.now(timezone.utc)
    if purchase["plan_type"] == "yearly":
        expires_at = (now + timedelta(days=365)).isoformat()
    else:
        expires_at = (now + timedelta(days=30)).isoformat()

    db.conn.execute(
        "INSERT OR REPLACE INTO basicfit_accounts (username, password, plan_type, buyer_id, buyer_name, purchased_at, expires_at, active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (username, password, purchase["plan_type"], purchase["user_id"], str(interaction.user), now.isoformat(), expires_at),
    )
    db.conn.commit()

    db.conn.execute(
        "UPDATE basicfit_purchases SET username = ?, password = ?, status = 'completed', completed_at = ? WHERE id = ?",
        (username, password, now.isoformat(), purchase["id"]),
    )
    db.conn.commit()

    instructions = (
        f">>> :warning: **Procedure a suivre :**\n\n"
        f"1. Va sur l'application/site **Basic-Fit**\n"
        f"2. Clique sur **Mot de passe oublie**\n"
        f"3. Fais la demande **directement**\n"
        f"4. **Le staff va changer le mot de passe pour toi**\n\n"
        f":fire: **Fais-le tout de suite !** Les emails sont temporaires et peuvent expirer."
    )

    embed = discord.Embed(title="Paiement confirme - Basic-Fit Ultimate", color=discord.Color.green())
    embed.add_field(name="Offre", value=f"**{plan_label}**", inline=True)
    embed.add_field(name="Identifiant", value=f"**`{username}`**", inline=False)
    embed.add_field(name="Mot de passe", value=f"**`{password}`**", inline=False)
    if bf_email:
        embed.add_field(name="Email Basic-Fit", value=f"`{bf_email}`", inline=False)
    if expires_at:
        embed.add_field(name="Expire le", value=expires_at, inline=True)
    embed.add_field(name="Instructions", value=instructions, inline=False)
    embed.set_footer(text="Contacte le staff pour toute question.")
    await interaction.channel.send(embed=embed)

    try:
        user = bot.get_user(purchase["user_id"]) or await bot.fetch_user(purchase["user_id"])
        dm = discord.Embed(title="Ton compte Basic-Fit Ultimate est pret !", color=BASICFIT_COLOR)
        dm.add_field(name="Offre", value=f"**{plan_label}**", inline=True)
        dm.add_field(name="Identifiant", value=f"**`{username}`**", inline=False)
        dm.add_field(name="Mot de passe", value=f"**`{password}`**", inline=False)
        if bf_email:
            dm.add_field(name="Email Basic-Fit", value=f"`{bf_email}`", inline=False)
        if expires_at:
            dm.add_field(name="Expire le", value=expires_at, inline=True)
        dm.add_field(
            name="Connexion au bot",
            value="Retourne sur le Discord > panel Basic-Fit > Connexion\nUtilise ton identifiant et mot de passe generes.",
            inline=False,
        )
        dm.add_field(
            name="Mot de passe oublie Basic-Fit",
            value=(
                "Va sur l'app Basic-Fit > Mot de passe oublie\n"
                "Fais la demande immediatement.\n"
                "Le staff va changer le mot de passe pour toi.\n\n"
                ":fire: Fais-le vite, les emails sont temporaires !"
            ),
            inline=False,
        )
        dm.set_footer(text="Ne perds pas tes identifiants de connexion au bot !")
        await user.send(embed=dm)
    except discord.DiscordException:
        await interaction.channel.send(f"Impossible d'envoyer un MP a <@{purchase['user_id']}>. Transmets lui ses identifiants.")

    if isinstance(interaction.channel, discord.TextChannel) and interaction.guild:
        try:
            paid_cat = await find_or_create_bf_category(
                interaction.guild,
                "Basic-Fit Payes",
                ("basicfit paye", "bf paye", "basic-fit paye"),
            )
            await interaction.channel.edit(
                name=f"bf-paye-{purchase['id']:04d}",
                category=paid_cat,
                reason="Paiement Basic-Fit confirme",
            )
        except discord.DiscordException:
            pass

# ─── Admin Panel ──────────────────────────────────────────────

class BFAnnounceModal(discord.ui.Modal, title="Annonce a tous les abonnes"):
    message = discord.ui.TextInput(
        label="Message a envoyer",
        style=discord.TextStyle.paragraph,
        placeholder="Ecrit ton message ici...",
        max_length=1500,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: commands.Bot = interaction.client
        msg = str(self.message).strip()
        if not msg:
            await interaction.response.send_message("Message vide.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = bot.db.conn.execute(
            "SELECT DISTINCT buyer_id FROM basicfit_accounts WHERE active = 1"
        ).fetchall()
        sent = 0
        failed = 0
        for r in rows:
            try:
                user = bot.get_user(int(r["buyer_id"])) or await bot.fetch_user(int(r["buyer_id"]))
                dm = discord.Embed(
                    title="Annonce Basic-Fit",
                    description=msg,
                    color=BASICFIT_COLOR,
                )
                dm.set_footer(text="Basic-Fit Ultimate")
                await user.send(embed=dm)
                sent += 1
            except discord.DiscordException:
                failed += 1
            await asyncio.sleep(0.5)
        await interaction.followup.send(
            f"Annonce envoyee a **{sent}** abonne(s).",
            ephemeral=True,
        )
        if failed:
            await interaction.channel.send(f"{failed} abonne(s) non joignables (MP fermes).")


class BasicFitAdminView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Liste des comptes", style=discord.ButtonStyle.primary, emoji="\U0001f4cb", custom_id="ez:bf:admin:list")
    async def list_accounts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rows = bot.db.conn.execute(
                "SELECT * FROM basicfit_accounts ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            if not rows:
                await interaction.followup.send("Aucun compte pour le moment.", ephemeral=True)
                return
            lines = []
            for r in rows:
                plan = "Mensuel" if r["plan_type"] == "monthly" else "Annuel"
                status = "\U0001f7e2" if r["active"] else "\U0001f534"
                lines.append(f"{status} `{r['username']}` | {plan} | <@{r['buyer_id']}> | Fin: {r['expires_at'] or 'N/A'}")
            chunks = [lines[i:i+15] for i in range(0, len(lines), 15)]
            desc = "\n".join(chunks[0])
            if len(desc) > 4000:
                desc = desc[:3997] + "..."
            embed = discord.Embed(
                title=f"Comptes Basic-Fit ({len(rows)})",
                description=desc,
                color=BASICFIT_COLOR,
            )
            embed.set_footer(text=f"Page 1/{len(chunks)}")
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception:
            await interaction.followup.send("Erreur lors de la recuperation des comptes.", ephemeral=True)

    @discord.ui.button(label="Annonce MP", style=discord.ButtonStyle.success, emoji="\U0001f4e2", custom_id="ez:bf:admin:announce")
    async def announce(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        await interaction.response.send_modal(BFAnnounceModal())

    @discord.ui.button(label="Statistiques", style=discord.ButtonStyle.secondary, emoji="\U0001f4ca", custom_id="ez:bf:admin:stats")
    async def stats(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        bot: commands.Bot = interaction.client
        if not isinstance(interaction.user, discord.Member) or not is_bf_staff(bot, interaction.user):
            await interaction.response.send_message("Staff uniquement.", ephemeral=True)
            return
        total = bot.db.conn.execute("SELECT COUNT(*) FROM basicfit_accounts").fetchone()[0]
        actifs = bot.db.conn.execute("SELECT COUNT(*) FROM basicfit_accounts WHERE active = 1").fetchone()[0]
        monthly = bot.db.conn.execute("SELECT COUNT(*) FROM basicfit_accounts WHERE plan_type = 'monthly'").fetchone()[0]
        yearly = bot.db.conn.execute("SELECT COUNT(*) FROM basicfit_accounts WHERE plan_type = 'yearly'").fetchone()[0]
        revenu = bot.db.conn.execute("SELECT COALESCE(SUM(price), 0) FROM basicfit_purchases WHERE status = 'completed'").fetchone()[0]
        embed = discord.Embed(title="Statistiques Basic-Fit", color=BASICFIT_COLOR)
        embed.add_field(name="Total comptes", value=str(total), inline=True)
        embed.add_field(name="Actifs", value=str(actifs), inline=True)
        embed.add_field(name="Mensuels", value=str(monthly), inline=True)
        embed.add_field(name="Annuels", value=str(yearly), inline=True)
        embed.add_field(name="Revenu total", value=f"{revenu:.2f} EUR", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
