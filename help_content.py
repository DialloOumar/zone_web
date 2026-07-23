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
        "id": "admin_users",
        "icon": "group",
        "gate": {"super_admin": True},
        "title": {"fr": "Administration — Utilisateurs",
                  "en": "Administration — Users"},
        "intro": {
            "fr": "Réservé au super administrateur. Créez les comptes et donnez "
                  "les accès : un utilisateur reçoit un rôle par flotte, et c'est "
                  "ce rôle qui détermine ce qu'il peut voir et faire.",
            "en": "Super-admin only. Create accounts and grant access: a user gets "
                  "a role per fleet, and that role determines what they can see and do.",
        },
        "steps": {
            "fr": [
                "Ouvrez Administration → « Utilisateurs ».",
                "« Nouvel utilisateur » : nom complet, identifiant de connexion, mot de passe initial.",
                "Attribuez-lui une ou plusieurs flottes et, pour chacune, un rôle. Le rôle fixe ses permissions sur cette flotte.",
                "Enregistrez. L'utilisateur peut se connecter et changer son mot de passe.",
            ],
            "en": [
                "Open Administration → \"Users\".",
                "\"New user\": full name, login username, initial password.",
                "Assign one or more fleets and, for each, a role. The role sets their permissions on that fleet.",
                "Save. The user can sign in and change their password.",
            ],
        },
        "tips": {
            "fr": ["Archiver un utilisateur le désactive sans effacer son historique."],
            "en": ["Archiving a user disables them without erasing their history."],
        },
    },
    {
        "id": "admin_roles",
        "icon": "admin_panel_settings",
        "gate": {"perms": ["admin.roles"]},
        "title": {"fr": "Administration — Rôles et permissions",
                  "en": "Administration — Roles and permissions"},
        "intro": {
            "fr": "Un rôle est un ensemble de permissions. Pour chaque action "
                  "(voir, créer, modifier, supprimer…), vous choisissez l'un de "
                  "trois niveaux. C'est le cœur du contrôle d'accès.",
            "en": "A role is a set of permissions. For each action (view, create, "
                  "edit, delete…) you pick one of three levels. This is the core "
                  "of access control.",
        },
        "steps": {
            "fr": [
                "Ouvrez Administration → « Rôles » puis « Nouveau rôle ».",
                "Donnez un nom au rôle (ex. Conducteur, Superviseur).",
                "Pour chaque action, choisissez le niveau : Interdit (aucun accès), Direct (s'applique immédiatement) ou Validation (soumise à approbation avant d'être appliquée).",
                "Astuce : la ligne « Tout : » en haut d'un groupe règle toutes ses actions d'un coup.",
                "(Optionnel) Cochez « Peut approuver » pour que ce rôle puisse valider les demandes de sa flotte.",
                "Enregistrez. Le rôle devient attribuable aux utilisateurs.",
            ],
            "en": [
                "Open Administration → \"Roles\" then \"New role\".",
                "Name the role (e.g. Driver, Supervisor).",
                "For each action, pick the level: Cannot (no access), Direct (applies immediately) or Approval (sent for approval before applying).",
                "Tip: the \"All:\" row at the top of a group sets all its actions at once.",
                "(Optional) Tick \"Can approve\" so this role can review its fleet's requests.",
                "Save. The role can now be assigned to users.",
            ],
        },
        "tips": {
            "fr": ["C'est le rôle qui décide si une action part en « validation » — voir la rubrique Demandes et validations."],
            "en": ["The role decides whether an action goes to \"approval\" — see the Requests and approvals topic."],
        },
    },
    {
        "id": "admin_fleets",
        "icon": "workspaces",
        "gate": {"perms": ["admin.fleets"]},
        "title": {"fr": "Administration — Flottes et tarifs",
                  "en": "Administration — Fleets and rates"},
        "intro": {
            "fr": "Une flotte représente un client. C'est aussi là que se "
                  "définissent les tarifs de facturation, par catégorie d'engin "
                  "et par date.",
            "en": "A fleet represents a client. It's also where invoicing rates "
                  "are set, per vehicle category and per date.",
        },
        "steps": {
            "fr": [
                "Ouvrez Administration → « Flottes » puis « Nouvelle flotte ».",
                "Saisissez le nom du client.",
                "Cochez les catégories concernées et saisissez le tarif (GNF par voyage ou par heure) pour chacune.",
                "Indiquez la date d'effet du tarif. L'historique est conservé : un nouveau tarif n'écrase pas l'ancien, il s'applique à partir de sa date.",
                "Enregistrez. La facturation utilisera ces tarifs.",
            ],
            "en": [
                "Open Administration → \"Fleets\" then \"New fleet\".",
                "Enter the client name.",
                "Tick the relevant categories and enter the rate (GNF per trip or per hour) for each.",
                "Set the rate's effective date. History is kept: a new rate doesn't overwrite the old one, it applies from its date.",
                "Save. Invoicing will use these rates.",
            ],
        },
    },
    {
        "id": "admin_categories",
        "icon": "category",
        "gate": {"perms": ["admin.categories"]},
        "title": {"fr": "Administration — Catégories de véhicules",
                  "en": "Administration — Vehicle categories"},
        "intro": {
            "fr": "Une catégorie regroupe des engins du même type et fixe leur "
                  "mode de suivi — c'est-à-dire comment on compte leur activité.",
            "en": "A category groups machines of the same type and sets their "
                  "tracking mode — i.e. how their activity is counted.",
        },
        "steps": {
            "fr": [
                "Ouvrez Administration → « Catégories de véhicules » puis « Nouvelle catégorie ».",
                "Saisissez un code (ex. BUS, EXC) et les libellés.",
                "Choisissez le mode de suivi : Voyages (on compte des rotations), Heures (on saisit directement les heures) ou Index d'horomètre (on saisit l'index début/fin, les heures sont calculées).",
                "(Optionnel) Renseignez une consommation de référence (L/unité) pour les analyses de carburant.",
                "Enregistrez. La catégorie devient assignable aux véhicules.",
            ],
            "en": [
                "Open Administration → \"Vehicle categories\" then \"New category\".",
                "Enter a code (e.g. BUS, EXC) and the labels.",
                "Choose the tracking mode: Trips (count rotations), Hours (enter worked hours directly) or Hour-meter index (enter start/end index, hours are computed).",
                "(Optional) Set a baseline consumption (L/unit) for fuel analysis.",
                "Save. The category can now be assigned to vehicles.",
            ],
        },
        "tips": {
            "fr": ["Le mode de suivi détermine les champs du pointage et les règles d'entretien applicables (voyages / heures)."],
            "en": ["The tracking mode drives the pointage fields and which maintenance rules apply (trips / hours)."],
        },
    },
    {
        "id": "admin_settings",
        "icon": "settings",
        "gate": {"super_admin": True},
        "title": {"fr": "Administration — Paramètres et journal d'activité",
                  "en": "Administration — Settings and activity log"},
        "intro": {
            "fr": "Réglages généraux de la plateforme et traçabilité des actions.",
            "en": "General platform settings and action traceability.",
        },
        "steps": {
            "fr": [
                "« Paramètres » : devise, langue par défaut, et délai de grâce (le temps pendant lequel l'auteur d'une saisie peut la corriger sans repasser par la validation).",
                "« Journal d'activité » : la trace horodatée de toutes les actions (qui a fait quoi, et quand) — utile pour le suivi et le contrôle.",
            ],
            "en": [
                "\"Settings\": currency, default language, and grace period (the time during which an entry's author can fix it without going through approval again).",
                "\"Activity log\": the timestamped trace of every action (who did what, and when) — useful for monitoring and control.",
            ],
        },
    },
]
