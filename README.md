# Eat Zone - Bot Discord Railway

Bot Discord pour panels commande/support, tickets, claim cuisto, paiements PayPal/Crypto, transcripts et recap salaire.

## Railway

1. Decompresse ce dossier.
2. Mets tous les fichiers a la racine d'un repo GitHub.
3. Railway > New Project > Deploy from GitHub repo.
4. Dans Railway > Variables, ajoute les valeurs de `.env.example` avec tes vrais tokens.
5. Railway lancera automatiquement `python main.py`.

Ne mets jamais `.env` sur GitHub.

## Commandes principales

- `/panel panel_type:commande channel:#salon` : panel commande.
- `/panel panel_type:support channel:#salon` : panel support.
- `/panel panel_type:influence channel:#salon` : panel statut dispo/attente/off.
- `/paypal montant` : message PayPal dans un ticket.
- `/crypto montant` : cree un paiement crypto OxaPay dans un ticket.
- `/confirm` : confirme manuellement un paiement.
- `/salary` : recap benefices/salaires.
- `/payout_due` : sommes a rembourser/payer aux cuistos.
- `/payout_paid membre` : marque les paiements d'un cuisto comme regles.
- `/lock`, `/unlock`, `/nuke` : gestion salon.
- `/ban`, `/kick`, `/mute`, `/unmute` : moderation de base.

## Volume Railway conseille

Pour garder la base SQLite apres redeploiement, ajoute un Volume Railway monte sur `/data`, puis mets :

```env
BOT_DATA_DIR=/data
DATABASE_PATH=/data/eatzone.db
TRANSCRIPTS_DIR=/data/transcripts
```

