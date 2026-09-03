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
        "id": "carburant",
        "icon": "local_gas_station",
        "gate": {"perms": ["expense.view"]},
        "title": {"fr": "Enregistrer un plein de carburant",
                  "en": "Record a fuel fill-up"},
        "intro": {
            "fr": "Le carburant a sa propre page parce que c'est la dépense la "
                  "plus fréquente et la seule qui se compte aussi en litres. Un "
                  "plein est toujours rattaché à un véhicule.",
            "en": "Fuel has its own page because it is the most frequent expense "
                  "and the only one also counted in litres. A fill-up is always "
                  "tied to a vehicle.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Carburant » puis « Nouveau plein ».",
                "Choisissez le véhicule et la date.",
                "Saisissez le montant (GNF) et les litres. Le prix au litre s'affiche tout seul dans la liste — c'est un bon contrôle de cohérence.",
                "(Optionnel) Indiquez le conducteur qui a fait le plein.",
                "Choisissez le moyen de paiement (mobile money, espèces, virement, chèque, autre) et, si vous en avez une, la référence (numéro de transaction, de reçu…).",
                "Enregistrez.",
            ],
            "en": [
                "Open \"Fuel\" then \"New fill-up\".",
                "Pick the vehicle and the date.",
                "Enter the amount (GNF) and the litres. The price per litre is computed for you in the list — a handy sanity check.",
                "(Optional) Name the driver who filled up.",
                "Pick the payment method (mobile money, cash, transfer, cheque, other) and, if you have one, the reference (transaction or receipt number).",
                "Save.",
            ],
        },
        "tips": {
            "fr": [
                "Le total du mois et le total des litres sont rappelés en haut de la liste.",
                "Les pleins alimentent l'analyse carburant.",
                "Sur les citernes, la saisie n'est jamais refusée : si une distribution vide la citerne au-delà de ce qu'elle avait, ou si une rentrée dépasse sa capacité, c'est enregistré et signalé. Le stock s'affiche alors en rouge, avec ce qu'il faut faire.",
                "Un stock négatif veut dire qu'il manque une rentrée : saisissez-la à sa vraie date et le compte se remet d'aplomb tout seul.",
                "Le badge « Historique à régulariser » apparaît quand le stock d'aujourd'hui est correct mais que la citerne est sortie de ses bornes à une date passée. Il mène droit aux mouvements concernés, surlignés dans la liste.",
            ],
            "en": [
                "The month's total and total litres are shown at the top of the list.",
                "Fill-ups feed the fuel analysis.",
                "On citernes, entry is never refused: a distribution that takes more than the tank held, or a fill past its capacity, is recorded and flagged. The stock then shows in red, with what to do about it.",
                "A negative stock means a rentrée is missing: log it on its real date and the figure settles on its own.",
                "The \"History to settle\" badge shows when today's stock is fine but the citerne went out of bounds on a past date. It leads straight to the movements, highlighted in the list.",
            ],
        },
    },
    {
        "id": "depenses",
        "icon": "payments",
        "gate": {"perms": ["expense.view"]},
        "title": {"fr": "Enregistrer une dépense", "en": "Record an expense"},
        "intro": {
            "fr": "« Dépenses » est le registre unique de tous les coûts : ce que "
                  "vous saisissez ici, plus les pleins de carburant, les coûts "
                  "d'entretien et les achats de pièces, qui y arrivent "
                  "automatiquement. On y saisit les frais généraux — ceux qui "
                  "ne sont rattachés à aucune machine.",
            "en": "\"Expenses\" is the single ledger for every cost: what you enter "
                  "here, plus fuel fill-ups, service costs and parts purchases, "
                  "which land in it automatically. What you enter here is the "
                  "overheads — the costs tied to no machine.",
        },
        "steps": {
            "fr": [
                "La page « Dépenses », c'est la caisse : on y met de l'argent, et les dépenses le consomment. Le solde est affiché en haut.",
                "« Approvisionner la caisse » : date, montant, qui a remis l'argent, espèces ou mobile money. Le solde monte d'autant.",
                "Ouvrez « Dépenses » puis « Nouvelle dépense » pour une sortie.",
                "Donnez un nom à la dépense (ex. « Loyer du bureau », « Fournitures »). C'est ce nom qui la décrit — il n'y a pas de catégorie à choisir.",
                "Choisissez la flotte si la dépense en concerne une. Sans flotte, c'est une dépense de société, visible par toute personne ayant accès à la page.",
                "Entrez la date et le montant, puis le moyen de paiement et la référence.",
                "(Optionnel) La description accepte plusieurs lignes, pour détailler ce que recouvre la dépense.",
                "Enregistrez.",
            ],
            "en": [
                "The \"Expenses\" page is the cash box: money is paid in, and the costs spend it. The balance is at the top.",
                "\"Add money to the box\": date, amount, who handed it over, cash or mobile money. The balance goes up by that much.",
                "Open \"Expenses\" then \"New expense\" for money going out.",
                "Name the expense (e.g. \"Office rent\", \"Supplies\"). That name is what describes it — there is no category to pick.",
                "Pick the fleet if the cost belongs to one. With no fleet it is a company cost, visible to anyone with access to the page.",
                "Enter the date and amount, then the payment method and reference.",
                "(Optional) The description takes several lines, to spell out what the cost covers.",
                "Save.",
            ],
        },
        "tips": {
            "fr": [
                "Les coûts d'entretien ne se saisissent PAS ici : ils se saisissent sur la fiche d'entretien et apparaissent ensuite dans cette liste, catégorie « Entretien », en lecture seule. Pour corriger un tel montant, passez par la fiche d'entretien.",
                "Seules les dépenses saisies ici sortent de la caisse. Le carburant, les entretiens et les achats de pièces sont dans le registre mais pas dans la caisse, et n'apparaissent plus sur cette page.",
                "Le paiement se fait en espèces ou en mobile money. Les autres moyens ne sont plus proposés ici.",
                "Si le solde passe en négatif, il s'affiche en rouge : c'est qu'il manque un dépôt à enregistrer.",
                "« Rapport » ouvre le cahier de caisse imprimable : solde d'ouverture, chaque mouvement avec le solde après lui, solde de clôture, et deux lignes de signature. « Enregistrer au format PDF » dans la fenêtre d'impression en fait un fichier.",
                "Le rapport suit les filtres de la page. Choisissez un mois, ou une période « du / au » pour une plage précise — la période saisie l'emporte sur le mois.",
                "Les achats de pièces ne se saisissent pas ici non plus : ils partent de « Stock pièces », catégorie « Achat de pièces », également en lecture seule.",
                "Le carburant a sa propre page — voir la rubrique précédente. Il apparaît aussi dans cette liste si vous filtrez dessus.",
                "Une dépense de cette page n'est rattachée à aucune machine. Les coûts d'un véhicule se suivent depuis sa fiche : carburant, entretien et pièces y sont déjà.",
                "Le filtre en haut isole une catégorie. Les catégories accident et lavage restent affichées pour l'historique, même si elles ne se saisissent plus.",
            ],
            "en": [
                "Service costs are NOT entered here: you enter them on the service record and they then appear in this list under \"Maintenance\", read-only. To fix such an amount, go through the service record.",
                "Only the costs entered here come out of the cash box. Fuel, services and parts purchases are in the ledger but not in the box, and no longer show on this page.",
                "Payment is cash or mobile money. The other methods are no longer offered here.",
                "If the balance goes negative it shows in red: a deposit is missing.",
                "\"Report\" opens the printable cash book: opening balance, every movement with the balance after it, closing balance, and two signature lines. \"Save as PDF\" in the print dialog turns it into a file.",
                "The report follows the page's filters. Pick a month, or a from/to range for an exact window — the range wins over the month.",
                "Parts purchases are not entered here either: they come from \"Parts stock\" under \"Parts purchase\", also read-only.",
                "Fuel has its own page — see the previous topic. It also shows in this list if you filter for it.",
                "A cost on this page belongs to no machine. A vehicle's costs are followed from its own sheet, where fuel, services and parts already are.",
                "The filter at the top isolates a category. The accident and washing categories stay listed for history, even though nothing new is filed under them.",
            ],
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
                "Sur une alerte : « Enregistrer l'entretien » (l'intervention a été faite → l'alerte se résout et le compteur repart à zéro), « Reporter » ou « Ignorer ».",
                "« Reporter » range vraiment l'alerte de côté : elle quitte l'onglet « Actives » et le badge du menu, et attend dans l'onglet « Reportées » avec sa date de retour. Utile quand la pièce est commandée mais pas encore arrivée.",
                "Historique entretien : la liste de toutes les interventions (type, date, coût, conducteur).",
                "Sur la fiche d'entretien, vous pouvez associer le conducteur concerné, et saisir le coût avec son moyen de paiement.",
                "« Pièces » : ajoutez les pièces prises au magasin, une ligne par article. Elles sortent du stock et sont valorisées automatiquement au coût moyen. Le champ « Coût » ne concerne alors que la main-d'œuvre et la prestation du garage.",
            ],
            "en": [
                "Maintenance rules: define when to alert (e.g. oil change every 250 h, or every 5000 km, or every X trips, or every N days). A rule targets a vehicle, a category, a fleet or all.",
                "Alerts: when the running total (from the entries) nears or passes the interval, an alert shows under \"Alerts\" (a menu badge shows the active count).",
                "On an alert: \"Log the service\" (it was done → the alert resolves and the counter resets), \"Snooze\" or \"Dismiss\".",
                "\"Snooze\" genuinely puts the alert aside: it leaves the \"Active\" tab and the menu badge, and waits under \"Snoozed\" with its return date. Handy when the part is ordered but hasn't arrived.",
                "Service history: the list of all interventions (type, date, cost, driver).",
                "On the service record you can link the driver involved, and enter the cost with its payment method.",
                "\"Parts\": add the parts taken from the store, one line per item. They leave the stock and are valued automatically at the average cost. The \"Cost\" field then covers labour and outside work only.",
            ],
        },
        "tips": {
            "fr": [
                "Une alerte ne se résout QUE par un entretien enregistré (ou automatiquement si elle n'est plus due).",
                "Le coût saisi sur une fiche d'entretien part automatiquement dans « Dépenses », catégorie « Entretien ». Vous n'avez rien à ressaisir.",
                "Les règles en heures ne s'appliquent qu'aux engins suivis en heures, et les règles en voyages qu'aux engins suivis en voyages — c'est le mode de suivi de la catégorie qui décide.",
            ],
            "en": [
                "An alert only resolves by logging a service (or automatically once it's no longer due).",
                "The cost entered on a service record automatically lands in \"Expenses\" under \"Maintenance\". Nothing to re-enter.",
                "Hour-based rules only apply to machines tracked in hours, and trip-based rules only to machines tracked in trips — the category's tracking mode decides.",
            ],
        },
    },
    {
        "id": "stock",
        "icon": "shelves",
        "gate": {"perms": ["stock.view"]},
        "title": {"fr": "Stock des pièces d'entretien",
                  "en": "Maintenance parts stock"},
        "intro": {
            "fr": "Le magasin tient le compte des pièces : ce qui entre, ce qui "
                  "part sur les machines, et ce qu'il reste. Il est commun à "
                  "toute la société — une pièce n'appartient pas à une flotte.",
            "en": "The store keeps count of the parts: what comes in, what goes "
                  "onto the machines, and what is left. It is shared by the whole "
                  "company — a part does not belong to a fleet.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Stock pièces » puis « Nouvel article » : désignation, unité (pièce, litre, kg, jeu), seuil d'alerte et, si vous en avez déjà, la quantité de départ avec sa valeur unitaire.",
                "La quantité de départ, c'est ce que vous avez en magasin aujourd'hui. Elle ne crée pas de dépense : ces pièces ont été payées avant. Sa valeur unitaire sert à les compter dans le coût moyen.",
                "Il n'y a pas de prix « de catalogue » sur un article : un prix est toujours attaché à des pièces réelles, celles du stock de départ ou celles d'une réception.",
                "« Réception » : à chaque achat, saisissez la quantité, le prix réellement payé ce jour-là, le fournisseur et le moyen de paiement. C'est le seul moment où une pièce coûte de l'argent — la dépense part toute seule dans « Dépenses », catégorie « Achat de pièces ».",
                "Le prix est demandé à chaque réception, et pas une fois pour toutes, parce qu'il change. Chaque lot garde le sien, et le champ se pré-remplit avec le dernier prix payé.",
                "Les sorties se font depuis la fiche d'entretien, bloc « Pièces ». Elles ne créent aucune dépense : elles indiquent seulement quel véhicule a reçu la pièce.",
                "« Comptage » : quand vous comptez physiquement le magasin, saisissez ce que vous avez trouvé. Le stock est recalé sur ce chiffre et l'écart est indiqué.",
                "« Historique des mouvements » : tout est là, filtrable par article, par type et par période.",
            ],
            "en": [
                "Open \"Parts stock\" then \"New part\": designation, unit (piece, litre, kg, set), re-order level and, if you already have some, the opening quantity with its unit value.",
                "The opening quantity is what is in the store today. It creates no expense: those parts were paid for earlier. Its unit value is what makes them count towards the average cost.",
                "There is no catalogue price on a part: a price always belongs to real parts, either the opening stock or a receipt.",
                "\"Receipt\": on each purchase, enter the quantity, the price actually paid that day, the supplier and the payment method. This is the only moment a part costs money — the expense lands in \"Expenses\" under \"Parts purchase\" on its own.",
                "The price is asked on every receipt, not once and for all, because it changes. Each lot keeps its own, and the field pre-fills with the last price paid.",
                "Issues happen on the service record, in the \"Parts\" block. They create no expense: they only say which vehicle received the part.",
                "\"Count\": when you physically count the store, enter what you found. The stock is set to that figure and the gap is shown.",
                "\"Movement history\": everything is there, filterable by part, kind and period.",
            ],
        },
        "tips": {
            "fr": [
                "Une sortie est valorisée au coût moyen du stock au moment où elle a lieu, puis ce montant est figé. Un achat plus cher le mois suivant ne change donc jamais le coût d'une intervention déjà enregistrée.",
                "Si le stock ne suffit pas, la saisie passe quand même : un avertissement s'affiche et la quantité peut devenir négative. Enregistrez la réception manquante, ou faites un comptage, et le compte se remet d'aplomb.",
                "Le seuil d'alerte fait apparaître « à commander » sur la ligne de l'article. Sans seuil, aucun signalement.",
                "Supprimer une intervention remet ses pièces en stock ; supprimer une réception supprime aussi sa dépense.",
                "Un article sans aucun mouvement peut être supprimé pour de bon. Dès qu'il a une réception, une sortie ou un comptage, seul l'archivage reste possible — et le message vous dit ce qui le retient.",
            ],
            "en": [
                "An issue is valued at the stock's average cost at the moment it happens, and that amount is then frozen. A dearer purchase next month therefore never changes the cost of a service already recorded.",
                "If the shelf is short, the entry still goes through: a warning shows and the quantity may go negative. Log the missing receipt, or run a count, and the figure comes back straight.",
                "The re-order level shows \"re-order\" on the part's row. Without a level, nothing is flagged.",
                "Deleting a service puts its parts back in stock; deleting a receipt also deletes its expense.",
                "A part with no movement at all can be deleted for good. Once it has a receipt, an issue or a count, only archiving is left — and the message tells you what is holding it.",
            ],
        },
    },
    {
        "id": "dashboard",
        "icon": "space_dashboard",
        "gate": {"perms": ["dashboard.view"]},
        "title": {"fr": "Comprendre le tableau de bord",
                  "en": "Understand the dashboard"},
        "intro": {
            "fr": "Le tableau de bord répond à une seule question : est-ce que "
                  "les machines ont travaillé ce mois-ci, et est-ce que tout est "
                  "sous contrôle ? Il est purement opérationnel.",
            "en": "The dashboard answers one question: did the machines work this "
                  "month, and is everything under control? It is purely "
                  "operational.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Tableau de bord ».",
                "Les cartes du haut donnent le mois en cours : heures travaillées, voyages, kilomètres, carburant, véhicules actifs (combien ont tourné sur le total), alertes ouvertes et demandes à valider.",
                "« Activité mensuelle » : heures et voyages des 6 derniers mois. Deux unités, donc deux axes — les heures à gauche, les voyages à droite.",
                "« Véhicules les plus actifs » : les machines qui ont le plus tourné ce mois-ci, chacune dans son unité.",
                "« Saisies par jour » (30 derniers jours) : c'est le contrôle de la saisie elle-même. Un trou dans ce graphique = un jour où personne n'a pointé.",
                "« Consommation de carburant » : les litres par mois sur 6 mois.",
                "En bas : les alertes d'entretien ouvertes et les dernières saisies, avec un lien « Tout voir ».",
            ],
            "en": [
                "Open \"Dashboard\".",
                "The top cards cover the current month: worked hours, trips, kilometres, fuel, active vehicles (how many ran, out of the total), open alerts and requests to approve.",
                "\"Monthly activity\": hours and trips over the last 6 months. Two units, so two axes — hours on the left, trips on the right.",
                "\"Busiest vehicles\": the machines that ran the most this month, each in its own unit.",
                "\"Entries per day\" (last 30 days): this one checks the logging itself. A gap in this chart is a day nobody logged.",
                "\"Fuel consumption\": litres per month over 6 months.",
                "At the bottom: open maintenance alerts and the latest entries, each with a \"View all\" link.",
            ],
        },
        "tips": {
            "fr": ["Chaque carte ne compte que les flottes auxquelles vous avez accès. Deux personnes peuvent donc voir des chiffres différents — c'est normal."],
            "en": ["Every card only counts the fleets you have access to. Two people can therefore see different numbers — that is expected."],
        },
    },
    {
        "id": "analyse",
        "icon": "query_stats",
        "gate": {"perms": ["insights.view"]},
        "title": {"fr": "Analyse des opérations", "en": "Operations analysis"},
        "intro": {
            "fr": "L'analyse donne deux repères d'exploitation : la consommation "
                  "de chaque machine par rapport à sa référence, et le taux "
                  "d'utilisation du parc.",
            "en": "Analysis gives two operating indicators: each machine's "
                  "consumption against its baseline, and how much of the fleet "
                  "is actually being used.",
        },
        "steps": {
            "fr": [
                "Ouvrez « Analyse ».",
                "Choisissez le mois (et la flotte si vous en avez plusieurs).",
                "« Carburant vs référence » compare la consommation réelle de chaque véhicule à la consommation de référence de sa catégorie. Un écart qui se répète d'un mois sur l'autre signale souvent un besoin d'entretien (réglage moteur, fuite) ou un pointage incomplet.",
                "« Véhicules inactifs » liste les machines sans aucune saisie sur la période — soit elles sont réellement à l'arrêt, soit on a oublié de les pointer.",
            ],
            "en": [
                "Open \"Analysis\".",
                "Pick the month (and the fleet if you have several).",
                "\"Fuel vs baseline\" compares each vehicle's actual consumption against its category's baseline. A gap that repeats month after month usually points to a maintenance need (engine tuning, leak) or incomplete logging.",
                "\"Idle vehicles\" lists machines with no entry over the period — either they really are stopped, or someone forgot to log them.",
            ],
        },
        "tips": {
            "fr": ["La consommation de référence ne se saisit pas : elle sera déduite de l'activité réelle du parc après quelques mois de saisie. En attendant, la comparaison carburant reste vide."],
            "en": ["The reference consumption is not typed in: it will be derived from the fleet's own activity after a few months of logging. Until then the fuel comparison stays empty."],
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
                "Le dernier bloc, « Administration » (encadré en orange), donne accès aux écrans de configuration : gérer les flottes, gérer les catégories de véhicules, gérer les rôles. C'est ainsi qu'on délègue la configuration sans donner le compte super administrateur.",
                "Enregistrez. Le rôle devient attribuable aux utilisateurs.",
                "Pour retirer un rôle : « Archiver » le met de côté sans rien casser (réversible, les attributions sont conservées), « Supprimer » l'efface définitivement. La suppression n'est proposée que si personne ne porte le rôle — la colonne « Utilisateurs » vous le dit. Pour supprimer un rôle attribué, réattribuez d'abord ses utilisateurs.",
                "Les rôles livrés avec la plateforme (marqués « Système ») peuvent eux aussi être supprimés si vous ne les utilisez pas, et ils ne réapparaîtront pas à la prochaine mise à jour. Leur restauration demande en revanche une intervention technique.",
            ],
            "en": [
                "Open Administration → \"Roles\" then \"New role\".",
                "Name the role (e.g. Driver, Supervisor).",
                "For each action, pick the level: Cannot (no access), Direct (applies immediately) or Approval (sent for approval before applying).",
                "Tip: the \"All:\" row at the top of a group sets all its actions at once.",
                "(Optional) Tick \"Can approve\" so this role can review its fleet's requests.",
                "The last block, \"Administration\" (outlined in orange), opens the configuration screens: manage fleets, manage vehicle categories, manage roles. This is how you delegate setup without handing over the super-admin account.",
                "Save. The role can now be assigned to users.",
                "To remove a role: \"Archive\" sets it aside without breaking anything (reversible, assignments are kept), \"Delete\" erases it for good. Delete is only offered when nobody holds the role — the \"Users\" column tells you. To delete an assigned role, reassign its users first.",
                "The roles shipped with the platform (marked \"System\") can be deleted too if you don't use them, and they will not reappear on the next update. Restoring one, however, needs a technical intervention.",
            ],
        },
        "tips": {
            "fr": [
                "C'est le rôle qui décide si une action part en « validation » — voir la rubrique Demandes et validations.",
                "Les permissions du bloc Administration n'ont que deux états (Interdit / Direct) : un écran de configuration s'ouvre ou ne s'ouvre pas, il n'y a pas de validation derrière.",
                "Trois règles vous protègent d'une escalade de privilèges : vous ne pouvez accorder que des permissions que vous détenez vous-même ; vous ne pouvez pas modifier votre propre rôle (demandez au super administrateur) ; et « Peut approuver » ne se transmet que par quelqu'un qui l'a.",
                "La gestion des utilisateurs, les paramètres et le journal d'activité restent réservés au super administrateur et ne sont pas délégables.",
            ],
            "en": [
                "The role decides whether an action goes to \"approval\" — see the Requests and approvals topic.",
                "Administration permissions only have two states (Cannot / Direct): a configuration screen either opens or it doesn't; there is no approval step behind it.",
                "Three rules protect you from privilege escalation: you can only grant permissions you hold yourself; you cannot edit your own role (ask the super admin); and \"Can approve\" can only be passed on by someone who has it.",
                "User management, settings and the activity log stay super-admin-only and cannot be delegated.",
            ],
        },
    },
    {
        "id": "admin_fleets",
        "icon": "workspaces",
        "gate": {"perms": ["admin.fleets"]},
        "title": {"fr": "Administration — Flottes",
                  "en": "Administration — Fleets"},
        "intro": {
            "fr": "Une flotte est un regroupement de véhicules — un site, un "
                  "client. C'est l'unité de base des accès : un utilisateur "
                  "reçoit un rôle par flotte, et ne voit que ses flottes.",
            "en": "A fleet is a group of vehicles — a site, a client. It is the "
                  "unit access is built on: a user gets a role per fleet, and only "
                  "ever sees their own fleets.",
        },
        "steps": {
            "fr": [
                "Ouvrez Administration → « Flottes » puis « Nouvelle flotte ».",
                "Saisissez le nom (l'identifiant court est proposé tout seul, vous pouvez le corriger).",
                "Cochez les catégories d'engins que cette flotte exploite. Cela détermine ce qu'on pourra y rattacher.",
                "Enregistrez.",
                "Pour retirer une flotte du service, utilisez « Archiver » : elle disparaît des listes mais son historique reste intact, et vous pouvez la réactiver.",
            ],
            "en": [
                "Open Administration → \"Fleets\" then \"New fleet\".",
                "Enter the name (the short identifier is proposed for you; you can adjust it).",
                "Tick the vehicle categories this fleet operates. That decides what can be attached to it.",
                "Save.",
                "To take a fleet out of service use \"Archive\": it leaves the lists but its history stays intact, and you can reactivate it.",
            ],
        },
        "tips": {
            "fr": ["Cet écran n'est plus réservé au super administrateur : la permission « Gérer les flottes » peut être donnée à un rôle (voir Rôles et permissions)."],
            "en": ["This screen is no longer super-admin-only: the \"Manage fleets\" permission can be granted to a role (see Roles and permissions)."],
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
                "Enregistrez. La catégorie devient assignable aux véhicules.",
            ],
            "en": [
                "Open Administration → \"Vehicle categories\" then \"New category\".",
                "Enter a code (e.g. BUS, EXC) and the labels.",
                "Choose the tracking mode: Trips (count rotations), Hours (enter worked hours directly) or Hour-meter index (enter start/end index, hours are computed).",
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
