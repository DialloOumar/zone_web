"""French + English translations.

French is the default (target audience: Guinea mining site). Keys follow the
batmex_web convention: dotted namespaces (section.key).

Anything missing from a non-default language falls back through `get_t`'s
chained lookup in app.py.
"""

TRANSLATIONS = {
    # ── French (default) ──────────────────────────────────────────────────
    "fr": {
        # Brand
        "app.title": "Zone Web",
        "app.tagline": "Suivi de flotte",

        # Auth
        "auth.login":             "Connexion",
        "auth.logout":            "Déconnexion",
        "auth.username":          "Nom d'utilisateur",
        "auth.email":             "Courriel",
        "auth.email_optional":    "Courriel (facultatif)",
        "auth.password":          "Mot de passe",
        "auth.sign_in":           "Se connecter",
        "auth.invalid_credentials": "Identifiants invalides.",
        "auth.required":          "Veuillez vous connecter.",

        # Nav
        "nav.dashboard":            "Tableau de bord",
        "nav.vehicles":             "Véhicules",
        "nav.daily_entries":        "Saisie quotidienne",
        "nav.maintenance":          "Maintenance",
        "nav.maintenance_records":  "Historique entretien",
        "nav.maintenance_rules":    "Règles d'entretien",
        "nav.alerts":               "Alertes",
        "nav.expenses":             "Dépenses",
        "nav.reports":              "Rapports",
        "nav.approvals":            "Validations en attente",
        "nav.admin":                "Administration",

        # Admin
        "admin.users":      "Utilisateurs",
        "admin.fleets":     "Flottes",
        "admin.roles":      "Rôles",
        "admin.categories": "Catégories de véhicules",
        "admin.settings":   "Paramètres",
        "admin.audit_log":  "Journal d'audit",

        # Roles
        "role.super_admin": "Super admin",
        "role.user":        "Utilisateur",

        # Errors
        "error.forbidden":  "Accès refusé",
        "error.not_found":  "Page introuvable",

        # Common buttons / labels
        "btn.save":   "Enregistrer",
        "btn.cancel": "Annuler",
        "btn.edit":   "Modifier",
        "btn.delete": "Supprimer",
        "btn.new":    "Nouveau",
        "btn.approve": "Approuver",
        "btn.reject":  "Refuser",
        "btn.dismiss": "Ignorer",
        "btn.submit_for_approval": "Soumettre pour validation",

        # Forms
        "form.optional": "Facultatif",
        "form.required": "Obligatoire",
        "form.select":   "Sélectionner…",
        "form.search":   "Rechercher…",

        # Vehicle categories (master list labels for UI)
        "cat.bus":        "Bus",
        "cat.minibus":    "Minibus",
        "cat.navette":    "Navette",
        "cat.camion_tsf": "Camion TSF",
        "cat.machine_tsf": "Machine TSF",
        "cat.citerne":    "Citerne",
        "cat.service":    "Véhicule de service",
        "cat.autre":      "Autre",

        # Permissions categories (for the role grid)
        "perm.cat.vehicles":     "Véhicules",
        "perm.cat.entries":      "Saisies",
        "perm.cat.maintenance":  "Maintenance",
        "perm.cat.expenses":     "Dépenses",
        "perm.cat.reports":      "Rapports",
        "perm.cat.alerts":       "Alertes",
        "perm.cat.admin":        "Administration",

        # Photo errors (carried over from batmex)
        "photo.upload_required":             "Une photo du bon est obligatoire.",
        "photo.upload_required_with_reason": "La photo n'a pas pu être enregistrée, donc la saisie n'a pas été sauvegardée.",
        "photo.err.not_configured":          "Le stockage des photos n'est pas configuré sur le serveur — contacte l'admin.",
        "photo.err.too_large":               "Photo trop volumineuse (plus de 12 Mo). Reprends une photo ou choisis-en une plus petite.",
        "photo.err.bad_format":              "Format de photo non supporté (souvent un HEIC iPhone). Utilise l'appareil photo en JPEG, ou choisis une JPEG/PNG depuis la galerie.",
        "photo.err.s3_error":                "Impossible de joindre le stockage photo — problème de connexion. Réessaie quand le réseau est meilleur.",
        "photo.err.unknown":                 "Échec de l'envoi de la photo — erreur inconnue. Réessaie ; si ça persiste, contacte l'admin.",
    },

    # ── English ───────────────────────────────────────────────────────────
    "en": {
        "app.title": "Zone Web",
        "app.tagline": "Fleet tracking",

        "auth.login":             "Login",
        "auth.logout":            "Log out",
        "auth.username":          "Username",
        "auth.email":             "Email",
        "auth.email_optional":    "Email (optional)",
        "auth.password":          "Password",
        "auth.sign_in":           "Sign in",
        "auth.invalid_credentials": "Invalid credentials.",
        "auth.required":          "Please log in.",

        "nav.dashboard":            "Dashboard",
        "nav.vehicles":             "Vehicles",
        "nav.daily_entries":        "Daily entries",
        "nav.maintenance":          "Maintenance",
        "nav.maintenance_records":  "Maintenance history",
        "nav.maintenance_rules":    "Maintenance rules",
        "nav.alerts":               "Alerts",
        "nav.expenses":             "Expenses",
        "nav.reports":              "Reports",
        "nav.approvals":            "Pending approvals",
        "nav.admin":                "Administration",

        "admin.users":      "Users",
        "admin.fleets":     "Fleets",
        "admin.roles":      "Roles",
        "admin.categories": "Vehicle categories",
        "admin.settings":   "Settings",
        "admin.audit_log":  "Audit log",

        "role.super_admin": "Super admin",
        "role.user":        "User",

        "error.forbidden":  "Forbidden",
        "error.not_found":  "Not found",

        "btn.save":   "Save",
        "btn.cancel": "Cancel",
        "btn.edit":   "Edit",
        "btn.delete": "Delete",
        "btn.new":    "New",
        "btn.approve": "Approve",
        "btn.reject":  "Reject",
        "btn.dismiss": "Dismiss",
        "btn.submit_for_approval": "Submit for approval",

        "form.optional": "Optional",
        "form.required": "Required",
        "form.select":   "Select…",
        "form.search":   "Search…",

        "cat.bus":        "Bus",
        "cat.minibus":    "Minibus",
        "cat.navette":    "Shuttle",
        "cat.camion_tsf": "TSF Truck",
        "cat.machine_tsf": "TSF Machine",
        "cat.citerne":    "Water tanker",
        "cat.service":    "Service vehicle",
        "cat.autre":      "Other",

        "perm.cat.vehicles":     "Vehicles",
        "perm.cat.entries":      "Entries",
        "perm.cat.maintenance":  "Maintenance",
        "perm.cat.expenses":     "Expenses",
        "perm.cat.reports":      "Reports",
        "perm.cat.alerts":       "Alerts",
        "perm.cat.admin":        "Administration",

        "photo.upload_required":             "A photo of the slip is required.",
        "photo.upload_required_with_reason": "The photo could not be saved, so the entry was not recorded.",
        "photo.err.not_configured":          "Photo storage is not configured on the server — contact your admin.",
        "photo.err.too_large":               "Photo is too large (over 12 MB). Take a new photo or pick a smaller one.",
        "photo.err.bad_format":              "Photo format not supported (likely an iPhone HEIC). Use the camera as JPEG, or pick a JPEG/PNG from the gallery.",
        "photo.err.s3_error":                "Could not reach photo storage — connection problem. Try again when you have better signal.",
        "photo.err.unknown":                 "Photo upload failed — unknown error. Please retry; if it persists, contact your admin.",
    },
}
