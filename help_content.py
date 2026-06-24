"""Role-aware help content for the in-app Aide page.

One entry per topic. `gate` decides who sees it — reusing the same permission
model as the sidebar/onboarding, so a user only reads the help for what they can
actually do. Content is bilingual (fr/en); the route picks the active language.

`gate` is one of:
  {"always": True}          → everyone
  {"super_admin": True}     → super admin only
  {"perms": ["a", "b"]}     → anyone holding ANY of these permissions
"""

HELP_SECTIONS = [
    {
        "id": "intro",
        "icon": "fact_check",
        "gate": {"always": True},
        "title": {"fr": "Comment utiliser cette aide",
                  "en": "How to use this help"},
        "intro": {
            "fr": "Cette page explique, pas à pas, comment utiliser ZONE. Elle "
                  "n'affiche que les rubriques correspondant à vos accès. Vous "
                  "pouvez l'imprimer pour la consulter hors connexion.",
            "en": "This page explains, step by step, how to use ZONE. It only "
                  "shows the topics matching your access. You can print it for "
                  "offline reference.",
        },
        "steps": {
            "fr": [
                "Le sommaire en haut liste vos rubriques disponibles — cliquez pour y aller.",
                "Pour revoir le guide de démarrage par rôle, ouvrez « Revoir le guide » en bas du menu.",
                "Pour imprimer cette aide, utilisez l'impression de votre navigateur (Ctrl+P).",
            ],
            "en": [
                "The table of contents lists the topics available to you — click to jump.",
                "To replay the role-based getting-started guide, use \"Replay the guide\" at the bottom of the menu.",
                "To print this help, use your browser's print (Ctrl+P).",
            ],
        },
    },
    {
        "id": "pointage",
        "icon": "fact_check",
        "gate": {"perms": ["entry.view", "entry.create"]},
        "title": {"fr": "Faire un pointage (saisie quotidienne)",
                  "en": "Log daily activity (pointage)"},
        "intro": {
            "fr": "Le pointage, c'est enregistrer chaque jour l'activité d'un "
                  "véhicule : voyages, heures ou index d'horomètre, plus le "
                  "carburant/km et le conducteur. C'est la base de tout — les "
                  "alertes d'entretien et les analyses en dépendent.",
            "en": "Pointage means logging each day's activity for a vehicle: "
                  "trips, hours or hour-meter index, plus fuel/km and the driver. "
                  "Everything else (maintenance alerts, analytics) is built on it.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Pointage » dans le menu : vous voyez une carte par véhicule pour la journée.",
                "Choisissez la date en haut (flèches ‹ › ou le calendrier).",
                "Sur la carte d'un véhicule, cliquez « + » (ou « Toucher pour saisir »).",
                "Renseignez l'activité selon le type de machine : nombre de voyages, OU heures travaillées, OU index de début et de fin de l'horomètre (les heures sont alors calculées automatiquement).",
                "Choisissez le conducteur dans la liste (il doit avoir été enregistré au préalable).",
                "(Optionnel) Renseignez les kilomètres.",
                "Cliquez « Enregistrer ».",
            ],
            "en": [
                "Open \"Pointage\": you get one card per vehicle for the day.",
                "Pick the date at the top (‹ › arrows or the calendar).",
                "On a vehicle card, click \"+\" (or \"Tap to log\").",
                "Enter the activity for the machine type: number of trips, OR worked hours, OR the hour-meter start/end index (hours are then computed automatically).",
                "Pick the driver from the list (they must be registered first).",
                "(Optional) Enter the kilometers.",
                "Click \"Save\".",
            ],
        },
        "tips": {
            "fr": [
                "Si votre rôle l'exige, la saisie part en validation : elle apparaît dans « Mes demandes » (En attente) et devient définitive après approbation.",
                "Pour voir/filtrer toutes les saisies, utilisez « Saisie quotidienne ».",
            ],
            "en": [
                "If your role requires it, the entry goes to approval: it shows in \"My requests\" (Pending) and applies once approved.",
                "To view/filter all entries, use \"Daily entries\".",
            ],
        },
    },
    {
        "id": "vehicules",
        "icon": "directions_bus",
        "gate": {"perms": ["vehicle.view"]},
        "title": {"fr": "Gérer les véhicules", "en": "Manage vehicles"},
        "intro": {
            "fr": "Les véhicules sont les engins suivis (bus, camions, "
                  "excavatrices…). Chaque véhicule appartient à une flotte et à "
                  "une catégorie qui définit son mode de suivi (voyages / heures).",
            "en": "Vehicles are the tracked machines (buses, trucks, excavators…). "
                  "Each belongs to a fleet and a category that sets its tracking "
                  "mode (trips / hours).",
        },
        "steps": {
            "fr": [
                "Ouvrez « Véhicules ».",
                "Pour en ajouter un, cliquez « Nouveau véhicule ».",
                "Renseignez le code, la flotte, la catégorie et (optionnel) un conducteur par défaut.",
                "Enregistrez.",
                "Cliquez sur le code d'un véhicule pour ouvrir sa fiche : activité, dépenses et entretiens.",
            ],
            "en": [
                "Open \"Vehicles\".",
                "To add one, click \"New vehicle\".",
                "Fill in the code, fleet, category and (optional) a default driver.",
                "Save.",
                "Click a vehicle's code to open its sheet: activity, expenses and services.",
            ],
        },
        "tips": {
            "fr": ["Supprimer un véhicule l'archive (il disparaît de la liste) sans effacer son historique. Utilisez « Voir les archivés » pour le retrouver ou le réactiver."],
            "en": ["Deleting a vehicle archives it (hidden from the list) without erasing its history. Use \"Show archived\" to find or reactivate it."],
        },
    },
    {
        "id": "conducteurs",
        "icon": "badge",
        "gate": {"perms": ["operator.view"]},
        "title": {"fr": "Gérer les conducteurs", "en": "Manage drivers"},
        "intro": {
            "fr": "La liste des conducteurs alimente tous les menus « conducteur » "
                  "(pointage, entretien, dépense). Un conducteur appartient à une flotte.",
            "en": "The driver list feeds every \"driver\" dropdown (pointage, "
                  "service, expense). A driver belongs to a fleet.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Conducteurs ».",
                "« Nouveau conducteur » : nom, flotte, téléphone, n° de permis.",
                "Enregistrez.",
                "Cliquez sur un nom pour ouvrir sa fiche : activité du mois, véhicules conduits, entretiens et dépenses qui lui sont rattachés.",
            ],
            "en": [
                "Open \"Drivers\".",
                "\"New driver\": name, fleet, phone, license number.",
                "Save.",
                "Click a name to open the driver sheet: monthly activity, vehicles driven, and the services and expenses tied to them.",
            ],
        },
        "tips": {
            "fr": ["Archiver un conducteur conserve tout son historique (ses pointages restent intacts)."],
            "en": ["Archiving a driver keeps all their history (their entries stay intact)."],
        },
    },
    {
        "id": "depenses",
        "icon": "payments",
        "gate": {"perms": ["expense.view"]},
        "title": {"fr": "Enregistrer une dépense", "en": "Record an expense"},
        "intro": {
            "fr": "Une dépense est un coût d'exploitation qui n'est PAS un "
                  "entretien (carburant, accident, lavage, autre). Elle est "
                  "rattachée à une flotte et, en option, à un véhicule et à un conducteur.",
            "en": "An expense is an operating cost that is NOT a service (fuel, "
                  "accident, washing, other). It belongs to a fleet and, "
                  "optionally, to a vehicle and a driver.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Dépenses » puis « Nouvelle dépense ».",
                "Choisissez la catégorie, la flotte et le véhicule (laisser vide = dépense de flotte).",
                "Entrez la date et le montant. Pour le carburant, vous pouvez préciser les litres.",
                "(Optionnel) Choisissez le conducteur à qui imputer la dépense.",
                "Enregistrez.",
            ],
            "en": [
                "Open \"Expenses\" then \"New expense\".",
                "Pick the category, fleet and vehicle (leave empty = fleet-wide cost).",
                "Enter the date and amount. For fuel you can also enter the liters.",
                "(Optional) Pick the driver the cost is attributed to.",
                "Save.",
            ],
        },
        "tips": {
            "fr": ["Les coûts d'entretien ne se saisissent PAS ici : ils ont leur propre fiche dans « Historique entretien »."],
            "en": ["Maintenance costs are NOT entered here: they have their own record under \"Service history\"."],
        },
    },
    {
        "id": "entretien",
        "icon": "handyman",
        "gate": {"perms": ["alert.view", "maintenance_record.view", "maintenance_rule.view"]},
        "title": {"fr": "Entretien : alertes, historique et règles",
                  "en": "Maintenance: alerts, history and rules"},
        "intro": {
            "fr": "Le module entretien prévient quand une machine doit être "
                  "révisée et garde l'historique des interventions. Les alertes "
                  "se calculent à partir des pointages.",
            "en": "The maintenance module warns when a machine is due for service "
                  "and keeps the service history. Alerts are computed from the "
                  "daily entries.",
        },
        "steps": {
            "fr": [
                "Règles d'entretien : définissez quand alerter (ex. vidange toutes les 250 h, ou tous les 5000 km, ou tous les X voyages, ou tous les N jours). Une règle cible un véhicule, une catégorie, une flotte ou tous.",
                "Alertes : quand le cumul (issu des pointages) approche ou dépasse l'intervalle, une alerte apparaît dans « Alertes » (un badge sur le menu indique le nombre d'alertes actives).",
                "Sur une alerte : « Enregistrer l'entretien » (l'intervention a été faite → l'alerte se résout et le compteur repart à zéro), « Reporter » (la masquer 7 jours, elle revient ensuite) ou « Ignorer ».",
                "Historique entretien : la liste de toutes les interventions (type, date, coût, conducteur).",
            ],
            "en": [
                "Maintenance rules: define when to alert (e.g. oil change every 250 h, or every 5000 km, or every X trips, or every N days). A rule targets a vehicle, a category, a fleet or all.",
                "Alerts: when the running total (from the entries) nears or passes the interval, an alert shows under \"Alerts\" (a menu badge shows the active count).",
                "On an alert: \"Log the service\" (it was done → the alert resolves and the counter resets), \"Snooze\" (hide it for 7 days, then it returns) or \"Dismiss\".",
                "Service history: the list of all interventions (type, date, cost, driver).",
            ],
        },
        "tips": {
            "fr": ["Une alerte ne se résout QUE par un entretien enregistré (ou automatiquement si elle n'est plus due)."],
            "en": ["An alert only resolves by logging a service (or automatically once it's no longer due)."],
        },
    },
    {
        "id": "dashboard",
        "icon": "space_dashboard",
        "gate": {"perms": ["dashboard.view"]},
        "title": {"fr": "Comprendre le tableau de bord",
                  "en": "Understand the dashboard"},
        "intro": {
            "fr": "Le tableau de bord donne une vue d'ensemble : indicateurs "
                  "clés, alertes ouvertes et activité récente.",
            "en": "The dashboard gives an overview: key figures, open alerts and "
                  "recent activity.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Tableau de bord ».",
                "Les cartes du haut résument les chiffres importants.",
                "Les graphiques montrent les tendances.",
                "Les alertes d'entretien ouvertes sont rappelées ici.",
            ],
            "en": [
                "Open \"Dashboard\".",
                "The top cards summarise the key numbers.",
                "The charts show the trends.",
                "Open maintenance alerts are surfaced here too.",
            ],
        },
    },
    {
        "id": "analyse",
        "icon": "query_stats",
        "gate": {"perms": ["insights.view"]},
        "title": {"fr": "Analyse des opérations", "en": "Operations analysis"},
        "intro": {
            "fr": "L'analyse aide à repérer les anomalies : surconsommation de "
                  "carburant, coût par véhicule, machines sous-utilisées.",
            "en": "Analysis helps spot anomalies: fuel overconsumption, cost per "
                  "vehicle, under-used machines.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Analyse ».",
                "Choisissez le mois (et la flotte si vous en avez plusieurs).",
                "Lisez les trois volets : carburant vs référence, coût par véhicule, véhicules inactifs.",
            ],
            "en": [
                "Open \"Analysis\".",
                "Pick the month (and the fleet if you have several).",
                "Read the three panels: fuel vs baseline, cost per vehicle, idle vehicles.",
            ],
        },
    },
    {
        "id": "facturation",
        "icon": "request_quote",
        "gate": {"perms": ["invoicing.view"]},
        "title": {"fr": "Facturation", "en": "Invoicing"},
        "intro": {
            "fr": "La facturation calcule ce qu'il faut facturer à un client "
                  "(une flotte) selon les unités travaillées (voyages / heures) "
                  "et les tarifs datés de la flotte.",
            "en": "Invoicing computes what to bill a client (a fleet) from the "
                  "worked units (trips / hours) and the fleet's dated rates.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Facturation ».",
                "Choisissez le mois et la flotte (client).",
                "Le montant se calcule à partir des pointages et des tarifs en vigueur.",
            ],
            "en": [
                "Open \"Invoicing\".",
                "Pick the month and the fleet (client).",
                "The amount is computed from the entries and the rates in force.",
            ],
        },
        "tips": {
            "fr": ["Les tarifs se définissent sur la fiche de la flotte (Administration → Flottes)."],
            "en": ["Rates are set on the fleet's record (Administration → Fleets)."],
        },
    },
    {
        "id": "demandes",
        "icon": "pending_actions",
        "gate": {"always": True},
        "title": {"fr": "Demandes et validations", "en": "Requests and approvals"},
        "intro": {
            "fr": "Selon votre rôle, certaines actions (créer / modifier / "
                  "supprimer) ne sont pas appliquées immédiatement : elles "
                  "partent en validation.",
            "en": "Depending on your role, some actions (create / edit / delete) "
                  "are not applied immediately: they go to approval.",
        },
        "steps": {
            "fr": [
                "Quand vous soumettez une action soumise à validation, un message confirme « soumis pour validation ».",
                "Suivez l'état dans « Mes demandes » (le badge indique le nombre en attente) : En attente / Approuvées / Refusées.",
                "Tant qu'une demande est en attente sur un enregistrement, celui-ci est verrouillé (pas de nouvelle modification) jusqu'à traitement.",
                "(Approbateurs) « Validations en attente » liste les demandes à approuver ou refuser.",
            ],
            "en": [
                "When you submit an action subject to approval, a message confirms \"submitted for approval\".",
                "Track it in \"My requests\" (the badge shows the pending count): Pending / Approved / Rejected.",
                "While a request is pending on a record, that record is locked (no further change) until it's processed.",
                "(Approvers) \"Pending approvals\" lists the requests to approve or reject.",
            ],
        },
    },
    {
        "id": "administration",
        "icon": "settings",
        "gate": {"super_admin": True},
        "title": {"fr": "Administration", "en": "Administration"},
        "intro": {
            "fr": "Réservé au super administrateur : configurer la plateforme.",
            "en": "Super-admin only: configure the platform.",
        },
        "steps": {
            "fr": [
                "Utilisateurs : créer les comptes et leur attribuer un rôle par flotte.",
                "Flottes : créer les clients / flottes et leurs tarifs de facturation.",
                "Rôles : définir les permissions de chaque rôle (Autoriser / Validation / Interdit, action par action).",
                "Catégories de véhicules : définir les types d'engins et leur mode de suivi (voyages / heures / index).",
                "Paramètres : réglages généraux (devise, délai de grâce…).",
                "Journal d'audit : la trace de toutes les actions.",
            ],
            "en": [
                "Users: create accounts and assign a role per fleet.",
                "Fleets: create clients / fleets and their invoicing rates.",
                "Roles: set each role's permissions (Allow / Approval / Deny, action by action).",
                "Vehicle categories: define machine types and their tracking mode (trips / hours / index).",
                "Settings: general options (currency, grace period…).",
                "Audit log: the trace of every action.",
            ],
        },
    },
]
