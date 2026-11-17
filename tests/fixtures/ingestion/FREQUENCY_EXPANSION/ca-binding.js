let topojsonObjects = {};

// // let lang = d3.select("html").attr("lang")

// let language = d3.select("html").attr("lang")

// Define all base and constant variables

const margin = { top: 20, right: 30, bottom: 100, left: 60 },
width = 950 - margin.left - margin.right,
height = 500 - margin.top - margin.bottom;

// Create translation dictionary -- NOW MOVED THESE TO SHARED FUNCTIONS JS
// const eftDict = language == "en" ?
// // If English, use EN-translator
// {
//     "Grippe": "Influenza",
//     " positif": " positive",

//     // From ENFRTranslate
//     " total": "",
//     "regional": "region",
//     "viral": "virus",

// }
// :
// // If French, use FR-translator
// {

//     "Percent positivity": "Pourcentage de positivité",
//     "percent positivity": "pourcentage de positivité",
//     "Percent of tests positive": "Pourcentage de tests positifs",
//     "percentage of tests positive": "pourcentage de tests positifs",
//     "Percentage of tests positive for": "Pourcentage de tests positifs du",
//     "Percentage of tests positive for ": "Pourcentage de tests positifs du ",
//     "detections": "détections",
//     "Trend": "La tendance",
//     ": ": " : ",
//     "Viral percent positivity stable": "Le pourcentage de positivité virale stable",
//     "Viral percent positivity increasing": "Le pourcentage de positivité virale augmente",
//     "Viral percent positivity decreasing": "Le pourcentage de positivité virale diminue",
//     // "Influenza (flu)": "Grippe",
//     "Influenza": "Grippe",
//     "SARS-CoV-2 (COVID-19)": "SRAS-CoV-2 (COVID-19)",
//     "SARS-CoV-2": "SRAS-CoV-2",
//     "RSV": "VRS",
//     "HMPV": "MPVH",
//     "HPIV": "VPIH",
//     "%": " %",
//     "Week": "Semaine",
//     "Week ": "Semaine ",
//     "week": "semaine",
//     "week ": "semaine ",
//     "week ending": "semaine se terminant",
//     "Number of ": "Nombre de",
//     "for": "pour",
//     "Number": "Nombre",
//     "Count": "Compte",
//     "Surveillance week": "Semaine de surveillance",
//     "surveillance week": "semaine de surveillance",
//     "Week": "Semaine",
//     "week": "semaine",
//     "Percentage (%) of FluWatchers reporting cough and fever": "Pourcentage qui ont signalé une toux et de la fièvre (ActionGrippe)",
//     "Percentage of FluWatchers reporting symptoms (%)": "Pourcentage des participants à l'ActionGrippe rapportant des symptômes (%)",
//     "Percentage of tests positive (%)":"Pourcentage de tests positifs (%)",
//     "Percent of tests positive for influenza (%)":"Pourcentage de tests positifs pour la grippe (%)",
//     "Average (2016-2017 to 2019-2020)":"Moyenne (2016-2017 à 2019-2020)",
//     "Setting":"Milieu",
//     "setting":"milieu",
//     "influenza type":"type d'influenza",
//     "Number of outbreaks": "Nombre d’éclosions",
//     "Cumulative proportion of outbreaks": "Proportion d’éclosions",
//     "Cumulative proportion of outbreaks (%)": "Proportion d’éclosions (%)",
//     "Proportion of outbreaks": "Proportion d’éclosions",
//     "Long-term care": "Établissements de soins de longue durée",
//     "Acute care": "Établissements de soins actifs",
//     "Retirement home": "Établissements de retraite",
//     "Remote isolated": "Collectivités éloignées ou isolées",
//     "School daycare": "Garderie d'école",
//     "Long-term care": "Établissements de soins de longue durée",
//     "Acute_care": "Établissements de soins actifs",
//     "Retirement_home": "Établissements de retraite",
//     "Remote_isolated": "Collectivités éloignées ou isolées",
//     "School_daycare": "Garderie d'école",
//     "Long-term_care": "Établissements de soins de longue durée",
//     "Other": "Autres",
//     "Influenza": "Grippe",
//     "influenza": "grippe",
//     "RSV": "VRS",
//     "Epidemic threshold (5%)": "Seuil épidémique (5 %)",
//     "Age group": "Groupe d’âge",
//     "years": "ans",
//     "year": "an",
//     "Proportion of typed influenza detections": "Pourcentage des détections de la grippe A et B",
//     "Proportion of subtyped influenza detections": "Pourcentage des sous-types de la grippe A",
//     "Number of positive detections": "Nombre de détections positifs",
//     "Proportion of positive detections": "Pourcentage de détections positifs",
//     "Select all": "Selectionner tout",
//     "Deselect all": "Déselectionner tout",
//     "FluWatchers": "ActionGrippe",
//     "(% positive)": "(% positif)",
//     "(% reporting symptoms)": "(% rapportant des symptômes)",
//     "trend": "la tendance",
//     "RSV detections": "Détections du VRS",
//     "RSV (% positive)": "VRS (% positif)",
//     "SARS-CoV-2 detections":"Détections du SRAS-CoV-2",
//     "SARS-CoV-2 (% positive)":"SRAS-CoV-2 (% positif)",
//     "Suppressed": "Supprimé",
//     "Influenza activity": "Activité grippale",
//     "Sporadic": "Sporadique",
//     "Widespread": "Étendue",
//     "Localized": "Localisée",
//     "No data": "Aucune donnée",
//     "No activity": "Aucune activité",
//     "Province/territory": "Province/territoire",
//     "Activity level": "Niveau d'activité",
//     "region of": "région de",
//     "region": "région",
//     "Reporting region": "Région de surveillance de la grippe",
//     "Percentage of tests positive for influenza ": "Pourcentage de tests positif pour la grippe ",
//     "Percentage of tests positive for ": "Pourcentage de tests positif pour ",
//     "Percentage of tests positive for": "Pourcentage de tests positif pour",
//     // "Number of reported ": "Nombre de détections rapporté pour ",
//     " detections reported": " détections rapporté",
//     "Influenza A (Unsubtyped)": "Grippe A(Non sous-typé)",
//     "Influenza A (H3N2)": "Grippe A(H3N2)",
//     "Influenza A (H1N1)": "Grippe A(H1N1)",
//     "Influenza A(Unsubtyped)": "Grippe A(Non sous-typé)",
//     "Influenza A(H3N2)": "Grippe A(H3N2)",
//     "Influenza A(H1N1)": "Grippe A(H1N1)",
//     "Influenza B": "Grippe B",
//     "Influenza A (% positive)": "Grippe A (% positif)",
//     "Influenza B (% positive)": "Grippe B (% positif)",
//     "Percentage of tests positive for ": "Pourcentage de tests positif pour ",
//     "Number of tests positive": "Nombre de tests positif",
//     "Proportion of tests positive": "Proportion de tests positif",
//     "RSV": "VRS",
//     "N/A": "S/O",
//     "Not applicable": "Sans objet",
//     "0 to 4": "0 à 4",
//     "2 to 4": "2 à 4",
//     "20 to 44": "20 à 44",
//     "45 to 64": "45 à 64",
//     "5 to 19": "5 à 19",
//     "65 to 84": "65 à 84",
//     "Number of positive RSV detections": "Nombre de détections du VRS",
//     "Number of influenza detections": "Nombre de détections de la grippe",
//     "Proportion of typed influenza detections": "Proportion de détections de la grippe typée",
//     "Proportion of typed influenza detections": "Proportion de détections de la grippe sous-typée",
//     "Influenza A": "Grippe A",
//     "Influenza B": "Grippe B",
//     "among people aged": "parmi les personnes âgées de",
//     "Min./Max. range": "Fourchette de min./max.",
//     "Number of detections": "Nombre de détections",
//     "deselect": "déselectionner",
//     "Proportion of RSV detections": "Proportion de détections du VRS",
//     "Min./Max. (2016-2017 to 2019-2020)": "Min./Max. (2016-2017 à 2019-2020)",

//     // From ENFRTranslate:
//     "Select all": "Sélectionner tout",
//     "Deselect all": "Déselectionner tout",
//     "deselect": "déselectionner",
//     " (week ": " (semaine ",
//     "Surveillance week": "Semaine de surveillance",
//     "Percentage of tests positive (%)": "Pourcentage de tests positifs (%)",
//     "Percent of tests positive (%)": "Pourcentage de tests positifs (%)",
//     "Percentage of tests positive": "Pourcentage de tests positifs",
//     "Positive tests (%)": "Tests positifs",
//     "Number of detections": "Nombre de détections",
//     "Number of tests": "Nombre de tests de dépistage",
//     "No data available for the selected timeframe": "Aucune donnée disponible pour la période sélectionnée",
//     "N/A": "S/O",
//     // "N/A or suppressed":"S/O ou Supprimé",
//     "N/A or suppressed":"S/O ou masqué",
//     "Week ": "Semaine ",
//     "; ": " ; ",
//     ": ": " : ",
//     "Region/Province": "Région/Province",
//     "Region": "Région",
//     "Virus": "Virus",
//     "Week end-date": "Date de fin de la semaine",
//     "Percent positivity (%)": "Pourcentage de positivité (%)",
//     "percent positivity": "pourcentage de positivité",
//     "Percent of tests positive": "Pourcentage de tests positifs",
//     "Percentage of tests positive (%) for SARS-CoV-2": "Pourcentage de tests positifs (%) pour le SRAS-CoV-2",
//     " or less": " ou moins",
//     " or more": " ou plus",
//     " to ": " à ",
//     " for ": " pour ",
//     // " for ": "",
//     "percentage of ": "pourcentage de",
//     "number of ": "nombre de ",
//     "Pre-pandemic range": "Fourchette pré-pandémique",
//     "Pre-pandemic average": "Moyenne pré-pandémique",
//     "Data unavailable for selected season(s).": "Données indisponibles pour la ou les saisons sélectionnées.",
//     "Current season percent positivity (%)": "Pourcentage de positivité de la saison en cours (%)",
//     "2023-2024 percent positivity (%)": "Pourcentage de positivité 2023-2024 (%)",
//     "2022-2023 percent positivity (%)": "Pourcentage de positivité 2022-2023 (%)",
//     "2021-2022 percent positivity (%)": "Pourcentage de positivité 2021-2022 (%)",
//     "2020-2021 percent positivity (%)": "Pourcentage de positivité 2020-2021 (%)",
//     "Pre-pandemic average percent positivity (%)": "Pourcentage moyen de positivité pré-pandémique (%)",
//     "Pre-pandemic range of percent positivity (%)": "Fourchette de pourcentage de positivité pré-pandémique (%)",
//     "Reporting laboratory": "Laboratoire de déclaration",
//     // "tested": "testés",
//     "tested": "test",
//     "positive": "positif",
//     "detections": "détections",
//     "Data N/A for": "Données N/D pour",
//     "selected region": "la région sélectionnée",
//     "selected region": "la période sélectionnée",
//     "selected virus": "le virus sélectionné",
//     "data N/A": "données N/D",
//     "Territories": "Territoires",
//     "Prairies": "Prairies",
//     "Atlantic": "Atlantique",
//     // "Suppressed": "Supprimé",
//     "Suppressed": "Masqué",
//     "Suppr.": "Mas.",
//     // "Influenza (flu)": "Grippe",
//     "Influenza": "Grippe",
//     "Influenza": "Grippe",
//     "SARS-CoV-2 (COVID-19)": "SRAS-CoV-2 (COVID-19)",
//     "SARS-CoV-2": "SRAS-CoV-2",
//     "RSV": "VRS",
//     "HMPV": "MPVH",
//     "HPIV": "VPIH",
//     "Other HPIV": "Autre VPIH",
//     "%": " %",
//     "Total influenza" : "Grippe",
//     "Total influenza A " : "Grippe A ",
//     "Total influenza B " : "Grippe B ",
//     "Influenza A" : "Grippe A",
//     "Influenza B" : "Grippe B",
//     "current week": "semaine en cours",
//     "selected week": "semaine sélectionnée",
//     "season to date": "saison à ce jour",
//     "province and territory": "province et territoire",
//     "reporting laboratory": "laboratoire rapporteur",
//     "region": "région",
//     " (region ": " (région ",
//     "Atlantic": "Atlantique",
//     "Quebec": "Québec",
//     "British Columbia": "Colombie-Britannique",
//     "Territories": "Territoires",
//     "Newfoundland and Labrador": "Terre-Neuve-et-Labrador",
//     "Prince Edward Island": "Île-du-Prince-Édouard",
//     "Nova Scotia": "Nouvelle-Écosse",
//     "New Brunswick": "Nouveau-Brunswick",
//     "Northwest Territories": "Territoires du Nord-Ouest",
//     "PHOL - Ottawa": "SPOL - Ottawa",
//     "EORLA": "ALREO",
//     "PHOL - Kingston": "SPOL - Kingston",
//     "PHOL - Toronto": "SPOL - Toronto",
//     "PHOL - Sault Ste. Marie": "SPOL - Sault Ste. Marie",
//     "PHOL - Timmins": "SPOL - Timmins",
//     "PHOL - London": "SPOL - London",
//     "PHOL - Orillia": "SPOL - Orillia",
//     "PHOL - Thunder Bay": "SPOL - Thunder Bay",
//     "PHOL - Sudbury": "SPOL - Sudbury",
//     "PHOL - Hamilton": "SPOL - Hamilton",
//     "PHOL - Peterborough": "SPOL - Peterborough",
//     "fixed": "fixe",
//     "dynamic": "dynamique",
//     " and ": " et ",
//     "and ": "et ",
//     "A(UnS) ": "A(NonS) ",
//     "regional": "région",
//     "viral": "virus",
//     "week": "semaine",
//     "week ending": "la semaine se terminant le",

//     "Yes": "Oui",
//     "No": "Non",
//     "number": "nombre",
//     "Number": "Nombre",
//     "Year": "Année",


// }

// // Function for translating French/English text using dictionary above
// function eft(ogText) {

//     let t = eftDict[ogText];

//     if (typeof t !== 'undefined') {
//         return t
//     } else {
//         return ogText
//     }

// }



// Define formats for linegraphs and related variables, for english and french usage
const ogVirusFormatsEN = {
    "SARS-CoV-2": { "colour": "#335075", "stroke-pattern": "0", "displayName": "Severe acute respiratory syndrome coronavirus 2", "legendOrderY": 1, "legendOrderX": 0 },
    // "Influenza": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Influenza (flu)", "legendOrderY": 1, "legendOrderX": 475 },
    // "Influenza A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A (flu A)", "legendOrderY": 1, "legendOrderX": 625 },
    // "Influenza B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B (flu B)", "legendOrderY": 1, "legendOrderX": 800 },
    "Influenza": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Influenza", "legendOrderY": 1, "legendOrderX": 475 },
    "Influenza A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A", "legendOrderY": 1, "legendOrderX": 625 },
    "Influenza B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B", "legendOrderY": 1, "legendOrderX": 800 },
    "RSV": { "colour": "#9467bd", "stroke-pattern": "0", "displayName": "Respiratory syncytial virus", "legendOrderY": 2, "legendOrderX": 0 },
    "EV/RV": { "colour": "#8c564b", "stroke-pattern": "0", "displayName": "Enterovirus/rhinovirus", "legendOrderY": 2, "legendOrderX": 350 },
    "HPIV": { "colour": "#e377c2", "stroke-pattern": "0", "displayName": "Human parainfluenza virus", "legendOrderY": 2, "legendOrderX": 700 },
    "ADV": { "colour": "#17becf", "stroke-pattern": "0", "displayName": "Adenovirus", "legendOrderY": 3, "legendOrderX": 0 },
    "HMPV": { "colour": "#7f7f7f", "stroke-pattern": "0", "displayName": "Human metapneumovirus", "legendOrderY": 3, "legendOrderX": 350 },
    "HCoV": { "colour": "#6F0000", "stroke-pattern": "0", "displayName": "Human coronavirus", "legendOrderY": 3, "legendOrderX": 700 },
};

const secondaryVirusFormatsEN = {
    "SARS-CoV-2": { "colour": "#335075", "stroke-pattern": "0", "displayName": "Severe acute respiratory syndrome coronavirus 2", "legendOrderY": 1, "legendOrderX": 0 },
    // "Flu": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Influenza (flu)", "legendOrderY": 1, "legendOrderX": 475 },
    // "Flu A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A (flu A)", "legendOrderY": 1, "legendOrderX": 625 },
    // "Flu B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B (flu B)", "legendOrderY": 1, "legendOrderX": 800 },
    "Flu": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Influenza", "legendOrderY": 1, "legendOrderX": 475 },
    "Flu A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A", "legendOrderY": 1, "legendOrderX": 625 },
    "Flu B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B", "legendOrderY": 1, "legendOrderX": 800 },
    "RSV": { "colour": "#9467bd", "stroke-pattern": "0", "displayName": "Respiratory syncytial virus", "legendOrderY": 2, "legendOrderX": 0 },
    "EV/RV": { "colour": "#8c564b", "stroke-pattern": "0", "displayName": "Enterovirus/rhinovirus", "legendOrderY": 2, "legendOrderX": 350 },
    "HPIV": { "colour": "#e377c2", "stroke-pattern": "0", "displayName": "Human parainfluenza virus", "legendOrderY": 2, "legendOrderX": 700 },
    "ADV": { "colour": "#17becf", "stroke-pattern": "0", "displayName": "Adenovirus", "legendOrderY": 3, "legendOrderX": 0 },
    "HMPV": { "colour": "#7f7f7f", "stroke-pattern": "0", "displayName": "Human metapneumovirus", "legendOrderY": 3, "legendOrderX": 350 },
    "HCoV": { "colour": "#6F0000", "stroke-pattern": "0", "displayName": "Human coronavirus", "legendOrderY": 3, "legendOrderX": 700 },
};

const regionFormatsEN = {
    "Canada": { "colour": "#335075", "stroke-pattern": "0", "displayName": "Severe acute respiratory syndrome coronavirus 2", "legendOrderY": 1, "legendOrderX": 0 },
    "Atlantic": { "colour": "#e377c2", "stroke-pattern": "0", "displayName": "Human parainfluenza virus", "legendOrderY": 2, "legendOrderX": 700 },
    // "Quebec": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Influenza (flu)", "legendOrderY": 1, "legendOrderX": 475 },
    // "Ontario": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A (flu A)", "legendOrderY": 1, "legendOrderX": 625 },
    // "Prairies": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B (flu B)", "legendOrderY": 1, "legendOrderX": 800 },
    "Quebec": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Influenza", "legendOrderY": 1, "legendOrderX": 475 },
    "Ontario": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A", "legendOrderY": 1, "legendOrderX": 625 },
    "Prairies": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B", "legendOrderY": 1, "legendOrderX": 800 },
    "British Columbia": { "colour": "#9467bd", "stroke-pattern": "0", "displayName": "Respiratory syncytial virus", "legendOrderY": 2, "legendOrderX": 0 },
    "Territories": { "colour": "#8c564b", "stroke-pattern": "0", "displayName": "Enterovirus/rhinovirus", "legendOrderY": 2, "legendOrderX": 350 },
    "Adenovirus": { "colour": "#17becf", "stroke-pattern": "0", "displayName": "Adenovirus", "legendOrderY": 3, "legendOrderX": 0 },
};

const ogVirusFormatsFR = {
    "SARS-CoV-2": { "colour": "#335075", "stroke-pattern": "0", "displayName": "Coronavirus du syndrome respiratoire aigu sévère 2", "legendOrderY": 1, "legendOrderX": 0 },
    "Influenza": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Grippe", "legendOrderY": 1, "legendOrderX": 475 },
    "Influenza A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Grippe A", "legendOrderY": 1, "legendOrderX": 625 },
    "Influenza B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Grippe B", "legendOrderY": 1, "legendOrderX": 800 },
    "RSV": { "colour": "#9467bd", "stroke-pattern": "0", "displayName": "Virus respiratoire syncytial", "legendOrderY": 2, "legendOrderX": 0 },
    "EV/RV": { "colour": "#8c564b", "stroke-pattern": "0", "displayName": "Entérovirus/rhinovirus", "legendOrderY": 2, "legendOrderX": 350 },
    "HPIV": { "colour": "#e377c2", "stroke-pattern": "0", "displayName": "Virus parainfluenza humain", "legendOrderY": 2, "legendOrderX": 700 },
    "ADV": { "colour": "#17becf", "stroke-pattern": "0", "displayName": "Adénovirus", "legendOrderY": 3, "legendOrderX": 0 },
    "HMPV": { "colour": "#7f7f7f", "stroke-pattern": "0", "displayName": "Métapneumovirus humain", "legendOrderY": 3, "legendOrderX": 350 },
    "HCoV": { "colour": "#6F0000", "stroke-pattern": "0", "displayName": "Coronavirus humain", "legendOrderY": 3, "legendOrderX": 700 },
};

const secondaryVirusFormatsFR = {
    "SARS-CoV-2": { "colour": "#335075", "stroke-pattern": "0", "displayName": "Coronavirus du syndrome respiratoire aigu sévère 2", "legendOrderY": 1, "legendOrderX": 0 },
    "Flu": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Grippe", "legendOrderY": 1, "legendOrderX": 475 },
    "Flu A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Grippe A", "legendOrderY": 1, "legendOrderX": 625 },
    "Flu B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Grippe B", "legendOrderY": 1, "legendOrderX": 800 },
    "RSV": { "colour": "#9467bd", "stroke-pattern": "0", "displayName": "Virus respiratoire syncytial", "legendOrderY": 2, "legendOrderX": 0 },
    "EV/RV": { "colour": "#8c564b", "stroke-pattern": "0", "displayName": "Entérovirus/rhinovirus", "legendOrderY": 2, "legendOrderX": 350 },
    "HPIV": { "colour": "#e377c2", "stroke-pattern": "0", "displayName": "Virus parainfluenza humain", "legendOrderY": 2, "legendOrderX": 700 },
    "ADV": { "colour": "#17becf", "stroke-pattern": "0", "displayName": "Adénovirus", "legendOrderY": 3, "legendOrderX": 0 },
    "HMPV": { "colour": "#7f7f7f", "stroke-pattern": "0", "displayName": "Métapneumovirus humain", "legendOrderY": 3, "legendOrderX": 350 },
    "HCoV": { "colour": "#6F0000", "stroke-pattern": "0", "displayName": "Coronavirus humain", "legendOrderY": 3, "legendOrderX": 700 },
};

const regionFormatsFR = {
    "Canada": { "colour": "#335075", "stroke-pattern": "0", "displayName": "Coronavirus du syndrome respiratoire aigu sévère 2", "legendOrderY": 1, "legendOrderX": 0 },
    "Atlantic": { "colour": "#e377c2", "stroke-pattern": "0", "displayName": "Virus parainfluenza humain", "legendOrderY": 2, "legendOrderX": 700 },
    "Quebec": { "colour": "#ff7f0e", "stroke-pattern": "0", "displayName": "Grippe", "legendOrderY": 1, "legendOrderX": 475 },
    "Ontario": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Grippe A", "legendOrderY": 1, "legendOrderX": 625 },
    "Prairies": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Grippe B", "legendOrderY": 1, "legendOrderX": 800 },
    "British Columbia": { "colour": "#9467bd", "stroke-pattern": "0", "displayName": "Virus respiratoire syncytial", "legendOrderY": 2, "legendOrderX": 0 },
    "Territories": { "colour": "#8c564b", "stroke-pattern": "0", "displayName": "Entérovirus/rhinovirus", "legendOrderY": 2, "legendOrderX": 350 },
    "Adenovirus": { "colour": "#17becf", "stroke-pattern": "0", "displayName": "Adénovirus", "legendOrderY": 3, "legendOrderX": 0 },
};

let ogVirusFormats,
    secondaryVirusFormats,
    regionFormats;

language == "en" ? ogVirusFormats = ogVirusFormatsEN : ogVirusFormats = ogVirusFormatsFR
language == "en" ? secondaryVirusFormats = secondaryVirusFormatsEN : secondaryVirusFormats = secondaryVirusFormatsFR
language == "en" ? regionFormats = regionFormatsEN : regionFormats = regionFormatsFR

// SET UP LISTS FOR USE IN SMALL MULTIPLES

// Set up list for all regions that have had a small multiple graph created
let exploreRegionalGraphsCreated = [];

// Set up list for all viruses that have had a small multiple graph created
let exploreViralGraphsCreated = [];

// Set up list of the graphs created in the previous selection
let explorePreviousRegionalGraphs = [];

// Set up list of the legend items that exist for the small multiples graphs
let exploreLegendItems = [];

// Keep previous type of explore graph in variable
let previousType;


// VARIABLES FOR FIGURE 3

// Set up list of virus graphs created
let fig3GraphsCreated = []


// Set up options for measures dropdown

let measuresList = {
    'Percent of tests positive (%)': "percentpositive",
    'Number of detections': "detections",
    // 'Positive tests (cumulative count within season)': "Cumulative_PositiveTests_",
    'Number of tests': "tests",
    // 'Tests (cumulative count within season)': "Cumulative_Tests_",
}

// The reverse version of the above measures object is used to later include the measure type in the title
let reverseMeasuresList = {
    'percentpositive': 'Percent of tests positive (%)',
    'detections': 'Number of detections',
    'tests': 'Number of tests',
}

// const fig3LineFormats = {
//     "pp_PrePandemicAverage": { "colour": "#333", "stroke-pattern": "6 2" },
//     "2020-2021": { "colour": "#9467bd", "stroke-pattern": "0" },
//     "2021-2022": { "colour": "#17becf", "stroke-pattern": "0" },  // Previous colour: 2ca02c
//     "2022-2023": { "colour": "#af3c43", "stroke-pattern": "0" },
//     "2023-2024": { "colour": "#ff7f0e", "stroke-pattern": "0" },
//     "2024-2025": { "colour": "#335075", "stroke-pattern": "0" },
// };

let fig3UpdateCount = 0



// VARIABLES FOR TEST/DETECTIONS TABLE
let tableUpdateCount = 0



// VARIABLES FOR ALL TEXT DESCRIPTION TABLES
let fig1TextDescRuns = 0,
    fig2TextDescRuns = 0,
    fig3TextDescRuns = 0,
    deTextDescRuns = 0;


// SMALL MULTIPLES PARAMETERS
const smPadding = { top: 25, right: 25, bottom: 75, left: 75 }
const smPadding2 = { top: 50, right: 25, bottom: 50, left: 75 }   // 2 is used for data exploration
// const smPadding = { top: 25, right: 25, bottom: 75, left: 55 }
const smMargin = { top: 20, right: 20, bottom: 20, left: 20 }
// const fullSMWidth = 350
// const fullSMHeight = 350
const fullSMWidth = 550
const fullSMHeight = 400
const smWidth = fullSMWidth - smPadding.left - smPadding.right
const smHeight = fullSMHeight - smPadding.top - smPadding.bottom
const smHeight2 = fullSMHeight - smPadding.top - smPadding.bottom   // 2 is used for data exploration

// LINEGRAPH PARAMETERS
const lgMargin = { top: 20, right: 20, bottom: 250, left: 75 }
const fullLgWidth = 1000
const fullLgHeight = 650
const lgWidth = fullLgWidth - lgMargin.left - lgMargin.right
const lgHeight = fullLgHeight - lgMargin.top - lgMargin.bottom


// Map's mini/regional linegraph parameters

// const smPadding = { top: 25, right: 25, bottom: 75, left: 75 }

const fullSMWidthMap = 350
const fullSMHeightMap = 350
const smWidthMap = fullSMWidthMap - smPadding.left - smPadding.right
const smHeightMap = fullSMHeightMap - smPadding.top - smPadding.bottom



// Load all data as promises

const dataPromises = []

dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_UpdateDate.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-surveillance/updateDate.csv"))
// dataPromises.push(language == "en" ? d3.csv("/src/data/respiratory-virus-detections/RVD_SummaryText.csv") : d3.csv("/src/data/respiratory-virus-detections/RVD_SummaryText_FR.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_SummaryText.csv"))
dataPromises.push(d3.csv("/src/data/respiratory-virus-surveillance/SummaryText.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-surveillance/dev/SummaryText-DEV.csv")) // NEW DEV TEXT...
dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_WeeklyData.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/dev/RVD_WeeklyData.csv"))
dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_SummaryData.csv"))
dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_BaselineData.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/dev/RVD_BaselineData.csv"))

dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_CurrentWeekTable.csv"))
dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/RVD_YTDTable.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/dev/CurrentWeekTableOngoing.csv"))
// dataPromises.push(d3.csv("/src/data/respiratory-virus-detections/dev/YTDTableOngoing.csv"))

//additional promise for default canada map json and for praries and territories canada map json
// dataPromises.push(d3.json("/src/json/canadian-risk-factor-atlus/ProvincesTerritories.json"))  // In prod
// dataPromises.push(d3.json("/src/json/canadian-risk-factor-atlus/ProvincesTerritories_praries_territories.json"))  // In prod
dataPromises.push(d3.json("/src/json/ProvincesTerritories.json"))  // In dev
dataPromises.push(d3.json("/src/json/ProvincesTerritories_prairies_territories.json"))  // In dev


// // Read data as promises, then perform necessary functions
Promise.all(dataPromises).then(function(datasets) {
    // Identify datasets
    const updateDate = datasets[0];
    const textData = datasets[1];
    const weeklyData = datasets[2];
    const tableData = datasets[3];
    const baselineData = datasets[4];
    const currentWeekTableData = datasets[5];
    const ytdTableData = datasets[6];

    // Fill out text content -- for RVDSS!
    populateText(textData, updateDate, weeklyData)

    // Create the "table" -- actually summary bubbles for RVDSS!
    tables(tableData)

    // Process the numerical data for RVDSS
    processData(weeklyData, baselineData);

    // Update date and schedule
    // updateDateSchedule(updateDate)

    // Create figure 1 RVDSS
    // Create first graph, for the viral activity comparison section
    lgSetup(weeklyData, baselineData, updateDate)

    // Create figure 2 RVDSS
    //Figure 2
    //filling the default map json to be used with COVID virus
    let defaultMap = topojson.feature(datasets[7], datasets[7]["objects"]["ProvincesTerritories"]).features;
    topojsonObjects["ProvincesTerritories"] = defaultMap;

    //filling the praries and territories combined map json to be used with every other virus
    let combinedMap = topojson.feature(datasets[8], datasets[8]["objects"]["ProvincesTerritories"]).features;
    topojsonObjects["ProvincesTerritoriesPraries"] = combinedMap;

    // // renderMap(weeklyData)
    // renderRVDSSMap(weeklyData, ["SARS-CoV-2", "Influenza", "RSV"], "RVDSSMap1")
    // renderRVDSSMap(weeklyData, ["HPIV", "ADV", "HMPV", "EV/RV", "HCoV"], "RVDSSMap2")

    // // Create RVDSS Figure 3
    // // Set up the viral activity historical comparison visualization
    // viralActivityHistoricalComparisonVisSetUp(weeklyData, baselineData)

    // // Create RVDSS Figure 4
    // // Set up the small multiples (data exploration) vis/controls
    // dataExplorationSetUp(weeklyData, baselineData)

    // // Create small multiples (data exploration) vis
    // // dataExplorationVis(weeklyData)

    // // Create RVDSS Table 1
    // // Set up tests/detections table
    // tableSetup(currentWeekTableData, ytdTableData, baselineData)


});

// Function to process numerical data
function processData(weekly, baseline) {
    weekly.forEach(d => {
        d.region = d.region;
        d.province = d.province;
        d.week = +d.week;
        d.weekorder = +d.weekorder;
        d.year = +d.year;
        d.date = d.date;
        d.datetime = d3.timeParse("%Y-%m-%d")(d.date);
        d.virus = d.virus;
        d.tests = +d.tests;
        d.detections = +d.detections;
        d.percentpositive = +d.percentpositive;
        // d.percentpositive = parseFloat((+d.percentpositive)).toFixed(2);
        d.tests_text = d.tests;
        d.detections_text = d.detections;
        d.percentpositive_text = d.percentpositive;
        // d.regionflag = +d.regionflag;
        // d.provinceflag = +d.provinceflag;

    });

    baseline.forEach(d => {
        d.region = d.region;
        d.week = +d.Week;
        d.weekorder = +d.weekorder;
        d.virus = d.virus;
        // if (d.virus == "sarscov2") {
        //     d.virus = "SARS-CoV-2"
        // } else if (d.virus == "flu") {
        //     d.virus = "Influenza"
        // } else if (d.virus == "flua") {
        //     d.virus = "Influenza A"
        // } else if (d.virus == "flub") {
        //     d.virus = "Influenza B"
        // } else if (d.virus == "rsv") {
        //     d.virus = "RSV"
        // } else if (d.virus == "hpiv") {
        //     d.virus = "HPIV"
        // } else if (d.virus == "adv") {
        //     d.virus = "ADV"
        // } else if (d.virus == "hmpv") {
        //     d.virus = "HMPV"
        // } else if (d.virus == "evrv") {
        //     d.virus = "EV/RV"
        // } else if (d.virus == "hcov") {
        //     d.virus = "HCOV"
        // } else if (d.virus == "hcov") {
        //     d.virus = "HCOV"
        // }
        // d.current_week_end_date = d.current_week_end_date.replace("2025", "2024").replace("2026", "2025")
        // d["pp_2022-2023_text"] = d.percentpositive_2022_2023;
        // d["pp_2021-2022_text"] = d.percentpositive_2021_2022;
        // d["pp_2020-2021_text"] = d.percentpositive_2020_2021;
        // d["pp_2022-2023"] = +d3.format(".2f")(+d.percentpositive_2022_2023);
        // d["pp_2021-2022"] = +d3.format(".2f")(+d.percentpositive_2021_2022);
        // d["pp_2020-2021"] = +d3.format(".2f")(+d.percentpositive_2020_2021);
        d["pp_2024-2025_text"] = d.Season_2024_2025;
        d["pp_2023-2024_text"] = d.Season_2023_2024;
        d["pp_2022-2023_text"] = d.Season_2022_2023;
        d["pp_2021-2022_text"] = d.Season_2021_2022;
        d["pp_2020-2021_text"] = d.Season_2020_2021;
        d["pp_2024-2025"] = d.Season_2024_2025.toString() == "" ? "" : +d3.format(".2f")(+d.Season_2024_2025);
        d["pp_2023-2024"] = d.Season_2023_2024.toString() == "" ? "" : +d3.format(".2f")(+d.Season_2023_2024);
        d["pp_2022-2023"] = d.Season_2022_2023.toString() == "" ? "" : +d3.format(".2f")(+d.Season_2022_2023);
        d["pp_2021-2022"] = d.Season_2021_2022.toString() == "" ? "" : +d3.format(".2f")(+d.Season_2021_2022);
        d["pp_2020-2021"] = d.Season_2020_2021.toString() == "" ? "" : +d3.format(".2f")(+d.Season_2020_2021);
        d.pp_PrePandemicMaximum = (+d.percentpositive_prepandemicmaximum);
        d.pp_PrePandemicMinimum = (+d.percentpositive_prepandemicminimum);
        d.pp_PrePandemicAverage = (+d.percentpositive_prepandemicaverage);

    })

    return weekly, baseline
}





// Function to populate all text for RVDSS Summary...
function populateText(textData, updateDate, weeklyData) {

    textData = language == "en" ? textData.filter(d => d.Language == "English") : textData.filter(d => d.Language == "French")

    // Enter the last updated date
    d3.select(".last_updated_date")
    // .text(updateDate.columns[0])
    // .text(d3.timeFormat('%Y-%m-%d')(d3.timeParse("%m/%d/%Y %I:%M:%S")(updateDate.columns[0])))
    .text(d3.timeFormat('%Y-%m-%d')(d3.timeParse("%Y-%m-%d %I:%M:%S")(updateDate.columns[0])))

    d3.select(".dateModified")
    // .text(updateDate.columns[0])
    // .text(d3.timeFormat('%Y-%m-%d')(d3.timeParse("%m/%d/%Y %I:%M:%S")(updateDate.columns[0])))
    .text(d3.timeFormat('%Y-%m-%d')(d3.timeParse("%Y-%m-%d %I:%M:%S")(updateDate.columns[0])))

    let formatTime;
     if(language == "en")
        formatTime = d3.timeFormat("%B %-d, %Y, %-I %p ET");
    else{
        let locale = {
        "dateTime": "%a %e %b %Y %X",
        "": "%Y-%m-%d",
        "time": "%H:%M:%S",
        "periods": ["", ""],
        "days": ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"],
        "shortDays": ["dim", "lun", "mar", "mer", "jeu", "ven", "sam"],
        "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
        "shortMonths": ["jan", "fév", "mar", "avr", "mai", "jui", "jul", "aoû", "sep", "oct", "nov", "déc"],
        "decimal": ",",
        "thousands": " ",
        "grouping": [3]
        }
    // create custom locale formatter for time from the given locale options
    d3.timeFormatDefaultLocale(locale);
        formatTime = d3.timeFormat("%-d %B %Y");
    }
    // var parseTime = d3.timeParse("%Y-%m-%d %H:%M");
    // d3.select("#update_dateTime").text(formatTime(d3.timeParse("%Y-%m-%d")(updateDate.columns[0])).replace("PM", "pm").replace("AM", "am"))
    // d3.select("#update_dateTime").text(formatTime(d3.timeParse("%d/%m/%Y %I:%M:%S")(updateDate.columns[0])).replace("PM", "pm").replace("AM", "am"))
    // d3.select("#update_dateTime").text(formatTime(d3.timeParse("%m/%d/%Y %I:%M:%S")(updateDate.columns[0])).replace("PM", "pm").replace("AM", "am")) // Previous version, before updating format for consistency with CRVSR
    d3.select("#update_dateTime").text(formatTime(d3.timeParse("%Y-%m-%d %I:%M:%S")(updateDate.columns[0])).replace("PM", "pm").replace("AM", "am"))


    // Enter the most recent date with data
    let lastDateData = weeklyData.filter(d => d.weekorder == d3.max(weeklyData, d => +d.weekorder))[0]
    let firstDateData = weeklyData.filter(d => d.weekorder == d3.min(weeklyData, d => +d.weekorder))[0]

    d3.selectAll(".last_data_date")
        // .text(formatDate(weeklyData.filter(d => d.weekorder == d3.max(weeklyData, d => +d.weekorder))[0].date))
        .text(formatDate(lastDateData.date))
    d3.selectAll(".last_data_week")
        // .text(weeklyData.filter(d => d.weekorder == d3.max(weeklyData, d => +d.weekorder))[0].week)
        .text(lastDateData.week)
    d3.selectAll(".yearWeekPeriod")
        .text(firstDateData.year + "-" + firstDateData.week + eft(" to ") + lastDateData.year + "-" + lastDateData.week)

    // Filter the intro, summary, and category sections
    let rvdssText = textData.filter(d => d.Project == "RVDSS")
    let summText = rvdssText.filter(row => row.Section == "summary")
    let catText = rvdssText.filter(row => row.Section.includes("category"))


    // Fill the summary section
    // d3.select("#summary_title")  // No longer displaying dynamic title as desired
    // .text(function() {
    //     return summText.filter(row => row.Type == "title")[0].Text
    // })

    // d3.select("#summary_text")
    // .text(function() {
    //     return summText.filter(row => row.Type == "text")[0].Text
    // })


    // // Identify and group all categories to summarize -- NO LONGER INCLUDING ANY SUMMARY TEXT
    // let allCats = [...new Set(catText.map(d => d.Section))]

    // let catsGrouped = d3.group(catText, d => d.Section)

    // // Identify div container
    // let catDiv = d3.select("#summary_category_content")

    // // Loop through categories to fill out text content
    // allCats.forEach(function(cat) {

    //     let currentCatText = catsGrouped.get(cat)

    //     let catTitle = currentCatText.filter(d => d.Type == "title")[0].Text

    //     catDiv.append("p")
    //     .append("strong")
    //     // .attr("class", "summary_section_title")
    //     // catDiv.append("h3")
    //     .text(catTitle + eft(":"))

    //     // let listitems = currentCatText.filter(d =>d.Type == "listitem")
    //     let listitems = currentCatText.filter(d => d.Type.includes("listitem") && !d.Type.includes("sub"))

    //     catDiv.append("ul")
    //     .selectAll(".summaryListItems-"+catTitle.replace(/\s/g, "_").replace("(", "").replace(")", ""))
    //     .data(listitems)
    //     .enter()
    //     .append("li")
    //     .attr("class", "summaryListItems-"+catTitle.replace(/\s/g, "_").replace("(", "").replace(")", ""))
    //     .html((d) => {

    //         let listItemNum = d.Type.slice(d.Type.length - 1)

    //         // if (listitems.length > 1) {
    //         if (currentCatText.length > 1) {

    //             let sublistitems = currentCatText.filter(dd => dd.Type.includes("sub") && dd.Type.includes(listItemNum))

    //             if (sublistitems.length > 0) {

    //                 let listItemText = d.Text + "<ul>"

    //                 sublistitems.forEach(function(sli) {

    //                     listItemText = listItemText + "<li>" + sli.Text + "</li>"

    //                 })

    //                 return listItemText + "</ul>"

    //             } else {

    //                 return d.Text

    //             }

    //         } else {

    //             return d.Text

    //         }

    //     })

    // })


}



// Create the summary tables
function tables(data) {

    // Define virus information
    const virusInfoEN = {
        "SARS-CoV-2": { "altName": "Severe acute respiratory syndrome coronavirus 2 (the virus that causes COVID-19)" },
        // "Influenza": { "altName": "Influenza (flu)" },
        "Influenza": { "altName": "Influenza" },
        // "Influenza A": { "colour": "#2ca02c", "stroke-pattern": "0", "displayName": "Influenza A (Flu A)", "legendOrderY": 1, "legendOrderX": 625 },
        // "Influenza B": { "colour": "#d62728", "stroke-pattern": "0", "displayName": "Influenza B (Flu B)", "legendOrderY": 1, "legendOrderX": 800 },
        // "RSV": { "altName": "Respiratory syncytial virus (RSV)" },
        // "EV/RV": { "altName": "Enterovirus/rhinovirus (EV/RV)" },
        // "HPIV": { "altName": "Human parainfluenza virus (HPIV)" },
        // "ADV": { "altName": "Adenovirus (ADV)" },
        // "HMPV": { "altName": "Human metapneumovirus (HMPV)" },
        // "HCoV": { "altName": "Human coronavirus (HCoV)" },
        "RSV": { "altName": "Respiratory syncytial virus" },
        "EV/RV": { "altName": "Enterovirus/rhinovirus" },
        "HPIV": { "altName": "Human parainfluenza virus" },
        "ADV": { "altName": "Adenovirus" },
        "HMPV": { "altName": "Human metapneumovirus" },
        "HCoV": { "altName": "Human coronavirus" },
    };

    const virusInfoFR = {
        // "SRAS-CoV-2": { "altName": "Coronavirus du syndrome respiratoire aigu sévère 2 (le virus à l’origine de la COVID-19)" },
        // "Grippe": { "altName": "Grippe" },
        // "VRS": { "altName": "Virus respiratoire syncytial (VRS)" },
        // "EV/RV": { "altName": "Entérovirus/rhinovirus (EV/RV)" },
        // "VPIH": { "altName": "Virus parainfluenza humain (VPIH)" },
        // "ADV": { "altName": "Adénovirus (ADV)" },
        // "MPVH": { "altName": "Métapneumovirus humain (MPVH)" },
        // "HCoV": { "altName": "Coronavirus humain (HCoV)" },
        "SARS-CoV-2": { "altName": "Coronavirus du syndrome respiratoire aigu sévère 2 (le virus à l’origine de la COVID-19)" },
        "Influenza": { "altName": "Grippe" },
        // "RSV": { "altName": "Virus respiratoire syncytial (VRS)" },
        // "EV/RV": { "altName": "Entérovirus/rhinovirus (EV/RV)" },
        // "HPIV": { "altName": "Virus parainfluenza humain (VPIH)" },
        // "ADV": { "altName": "Adénovirus (ADV)" },
        // "HMPV": { "altName": "Métapneumovirus humain (MPVH)" },
        // "HCoV": { "altName": "Coronavirus humain (HCoV)" },
        "RSV": { "altName": "Virus respiratoire syncytial" },
        "EV/RV": { "altName": "Entérovirus/rhinovirus" },
        "HPIV": { "altName": "Virus parainfluenza humain" },
        "ADV": { "altName": "Adénovirus" },
        "HMPV": { "altName": "Métapneumovirus humain" },
        "HCoV": { "altName": "Coronavirus humain" },
    };

    let virusInfo;
    language == "en" ? virusInfo = virusInfoEN : virusInfo = virusInfoFR

    // Define viruses to be depicted in table 1 and in table 2

    let table1Viruses,
        table2Viruses;
    // lang == "en" ? table1Viruses = ["SARS-CoV-2", "Influenza", "RSV"] : table1Viruses = ["SRAS-CoV-2", "Grippe", "VRS"]
    // lang == "en" ? table2Viruses = ["HPIV", "ADV", "HMPV", "EV/RV", "HCoV"] : table2Viruses = ["VPIH", "ADV", "MPVH", "EV/RV", "HCoV"]
    // table1Viruses = ["SARS-CoV-2", "Influenza", "RSV"]
    table2Viruses = ["HPIV", "ADV", "HMPV", "EV/RV", "HCoV"]
    // table2Viruses = ["FluWatchers", "Outbreaks"]    // Data doesn't exist for this yet...

    // Filter the data for the most recent week and for Canada, and for either table
    // let currentWeekData1 = data.filter(d => table1Viruses.includes(d.virus));
    let currentWeekData2 = data.filter(d => table2Viruses.includes(d.virus));
    // let currentWeekData3 = data.filter(d => table2Viruses.includes(d.virus));   // Data doesn't exist for this yet

    // Sort table 1 and table 2 data by descending percent positive
    // currentWeekData1 = currentWeekData1.sort((a,b) => b["percentpositive"] - a["percentpositive"])
    currentWeekData2 = currentWeekData2.sort((a,b) => b["percentpositive"] - a["percentpositive"])
    // currentWeekData3 = currentWeekData3.sort((a,b) => b["percentpositive"] - a["percentpositive"])  // Data doesn't exist for this yet

    //     // Putting the 'top viruses' into CARDS now instead!!
    // //Create the cards
    // let table1Cards = d3.select("#virusSummary1")
    // .selectAll(".virusCards")
    // .data(currentWeekData1)
    // .enter()
    // .append("div")
    // .attr("class", "col-sm-4 virusCards")
    // .append("div")
    // .attr("class", "virusCardsInner")

    // // Add the text elements
    // table1Cards.append("p")
    // .attr("class", "virusCardTitle")
    // // .text(d => d.virus)
    // .html(d => {
    //     if (d.virus == "Influenza") {
    //         // return eft("Influenza (flu)")
    //         return eft("Influenza")
    //     } else if (d.virus == "SARS-CoV-2") {
    //         return `<abbr title="${virusInfo[d.virus].altName}">${eft("SARS-CoV-2 (COVID-19)")}</abbr>`
    //     } else {
    //         // return d.virus
    //         return `<abbr title="${virusInfo[d.virus].altName}">${eft(d.virus)}</abbr>`
    //     }
    // })

    // table1Cards.append("p")
    // .attr("class", "virusCardValue")
    // .text(d => {
    //     if (language=="en") {
    //         return parseFloat(d.percentpositive).toFixed(1) + eft("%")
    //     } else {
    //         return parseFloat(d.percentpositive).toFixed(1).replace(".", ",") + eft("%")
    //     }
    // })

    // table1Cards.append("p")
    // .attr("class", "virusCardMeasure")
    // // .text(eft("percent positivity"))
    // .text(eft("percentage of tests positive"))

    // // table1Cards.append("p")
    // table1Cards.append("div")
    // .append("p")
    // .attr("class", "virusCardTrend")
    // .html(function(d) {

    //     let trendVal = d.trend
    //     let trend;
    //     if (trendVal == "up") {
    //         trend = `<span class="trendArrow"><i class='fa-solid fa-arrow-up' style='transform:rotate(45deg); color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity increasing")}</span></span>`
    //     } else if (trendVal == "down") {
    //         trend = `<span class="trendArrow"><i class='fa-solid fa-arrow-down' style='transform:rotate(315deg); color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity decreasing")}</span></span>`
    //     } else {
    //         trend = `<span class="trendArrow"><i class='fa-solid fa-arrows-left-right' style='color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity stable")}</span></span>`
    //     }
    //     // console.log(d, trendVal, trend)
    //     // return "<strong>Trend:</strong>  " + trend
    //     return `<strong>${eft("Trend")}${eft(": ")}</strong> ${trend}`
    // })

    // // Add a clearfix after the (new) trend div (which allows for larger trend arrows)...
    // table1Cards.append("div")
    // .attr("class", "clearfix")


    //Create the second batch of cards!

        // FIRST, add new title to the tiles...
        d3.select("#virusSummary2Tile").append("div")
        .attr("class", "virusCardTitleDiv")
        .append("p")
        .attr("class", "virusCardTitle")
        .html(eft("Percentage of tests positive"))

    let table2Cards = d3.select("#virusSummary2Tile")
    .selectAll(".virusCards2")
    .data(currentWeekData2)
    .enter()
    // .append("div")
    // .attr("class", "col-sm-4 virusCards2")
    .append("div")
    .attr("class", "virusCards2")

    // Add the text elements
    table2Cards.append("p")
    .attr("class", "virusCardTitle2")
    .html(d => {
        return `<abbr title="${virusInfo[d.virus].altName}">${eft(d.virus)}</abbr>`
    })

    table2Cards.append("p")
    .attr("class", "virusCardValue2")
    .text(d => {
        if (language=="en") {
            return parseFloat(d.percentpositive).toFixed(1) + eft("%")
        } else {
            return parseFloat(d.percentpositive).toFixed(1).toString().replace(".", ",") + eft("%")
        }
    })

    // table1Cards.append("p")
    // .attr("class", "virusCardMeasure")
    // .text("percent positivity")

    table2Cards.append("p")
    .attr("class", "virusCardTrend2")
    .html(function(d) {

        let trendVal = d.trend
        let trend;
        if (trendVal == "up") {
            trend = `<span class="trendArrow"><i class='fa-solid fa-arrow-up' style='transform:rotate(45deg); color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity increasing")}</span></span>`
        } else if (trendVal == "down") {
            trend = `<span class="trendArrow"><i class='fa-solid fa-arrow-down' style='transform:rotate(315deg); color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity decreasing")}</span></span>`
        } else {
            trend = `<span class="trendArrow"><i class='fa-solid fa-arrows-left-right' style='color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity stable")}</span></span>`
        }
        // console.log(d, trendVal, trend)
        // return "<strong>Trend:</strong>  " + trend
        return `<strong>${eft("Trend")}${eft(": ")}</strong> ${trend}`
    })


    // // Create the third batch of cards!
    // let table3Cards = d3.select("#virusSummary3")
    // .selectAll(".virusCards")
    // // .data(currentWeekData3)   // Data doesn't exist for this yet, so mocking it up with data filter #1 below...
    // .data(currentWeekData1.slice(0,2))
    // .enter()
    // .append("div")
    // // .attr("class", "col-sm-4 virusCards")
    // .attr("class",() => {
    //     if (currentWeekData1.slice(0,2).length < 4) {
    //         let cardWidth = 12 / currentWeekData1.slice(0,2).length
    //         return "col-sm-" + cardWidth + " virusCards"
    //     } else {
    //         return "col-sm-4 virusCards"
    //     }
    // })
    // .append("div")
    // .attr("class", "virusCardsInner")

    // // Add the text elements
    // table3Cards.append("p")
    // .attr("class", "virusCardTitle")
    // .html((d, i) => {
    //     // return `<abbr title="${virusInfo[d.virus].altName}">${eft(d.virus)}</abbr>`
    //     if (i == 0) {
    //         return `${eft("FluWatchers")}`
    //     } else {
    //         return `${eft("Outbreaks")}`
    //     }
    // })

    // table3Cards.append("p")
    // .attr("class", "virusCardValue")
    // .text((d, i) => {
    //     // if (lang=="en") {
    //     //     return parseFloat(d.percentpositive).toFixed(1) + eft("%")
    //     // } else {
    //     //     return parseFloat(d.percentpositive).toFixed(1).toString().replace(".", ",") + eft("%")
    //     // }
    //     if (language=="en") {
    //         if (i == 0) {
    //             return parseFloat((+d.percentpositive * 0.5)).toFixed(1) + eft("%")
    //         } else {
    //             return Math.round((+d.percentpositive + 2)) * 5
    //         }
    //     } else {
    //         if (i == 0) {
    //             return parseFloat((+d.percentpositive * 0.5)).toFixed(1).toString().replace(".", ",") + eft("%")
    //         } else {
    //             return Math.round((+d.percentpositive + 2)) * 5
    //         }
    //     }
    // })

    // table3Cards.append("p")
    // .attr("class", "virusCardMeasure")
    // .text((d, i) => {
    //     if (i == 0) {
    //         return eft("percentage of FluWatchers who reported cough and fever")
    //     } else {
    //         return eft("number of reported outbreaks related to COVID-19, influenza, or RSV")
    //     }
    // })

    // table3Cards.append("p")
    // .attr("class", "virusCardTrend")
    // .html(function(d, i) {

    //     let trendVal = d.trend
    //     let trend;
    //     if (trendVal == "up") {
    //         trend = `<span class="trendArrow"><i class='fa-solid fa-arrow-up' style='transform:rotate(45deg); color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity increasing")}</span></span>`
    //     } else if (trendVal == "down") {
    //         trend = `<span class="trendArrow"><i class='fa-solid fa-arrow-down' style='transform:rotate(315deg); color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity decreasing")}</span></span>`
    //     } else {
    //         trend = `<span class="trendArrow"><i class='fa-solid fa-arrows-left-right' style='color:#333'></i><span class='wb-inv'>${eft("Viral percent positivity stable")}</span></span>`
    //     }

    //     // let expectedVal = d.expected
    //     let expectedVal = (i == 0 ? "above" : "below")
    //     let expected;
    //     if (expectedVal == "above") {
    //         expected = `<span>${eft("above expected levels")}</span>`
    //     } else if (expectedVal == "below") {
    //         expected = `<span>${eft("below expected levels")}</span>`
    //     } else {
    //         expected = `<span>${eft("near expected levels")}</span>`
    //     }
    //     // console.log(d, trendVal, trend)
    //     // return "<strong>Trend:</strong>  " + trend
    //     return `<strong>${eft("Trend")}${eft(": ")}</strong> ${trend} <br> ${eft("and")} ${expected}`
    // })



}




// Set up linegraph
function lgSetup(weeklyData, baselineData, updateDate) {

    // // For testing new season!
    // weeklyData.forEach(d => {
    //     d.date = d.date.replace("2025", "2026").replace("2024","2025")
    //     d.year = +(d.year.toString().replace("2025", "2026").replace("2024", "2025"))
    //     // d.datetime = d3.timeParse("%Y-%m-%d")(d.date);
    //     // d.season = "2025-2026"
    // })


    if (d3.max(weeklyData, d => +d.weekorder) < 4) {
        d3.select("#earlySeasonWarning")
        .style("opacity", 1)
        .style("display", "block")
    }


    const lgSVG = d3.select("#fig1")
    .append('svg')
    .attr('width', "100%")
    .attr('height', "100%")
    .attr("preserveAspectRatio", "xMinYMin meet")
    // .attr("viewBox", `0 0 ${fullLgWidth} ${fullLgHeight}`)
    .attr("viewBox", `0 0 ${fullLgWidth} ${fullLgHeight - 150}`)   // REMOVING SOME HEIGHT TO ACCOUNT FOR LEGEND REMOVAL <><>

    // Add g element to SVG to hold all elements
    let lgG = lgSVG.append('g')
        .attr("transform", `translate(${lgMargin.left}, ${lgMargin.top})`)


    // Create explanatory note to place on the graph for when there is no data available
    lgG.append("text")
        .attr("id", "dataNAText")
        .attr("y", 100)
        .attr("x", lgWidth / 2)
        .attr("text-anchor", "middle")
        .text("");

    // Check if it is the first week; set up dummy data if so
    if ([... new Set(weeklyData.map(d => d.weekorder))].length == 1) {
        // Determine new YYY-MM-DD date
        let firstDate = d3.timeParse('%Y-%m-%d')(weeklyData[0].date)
        let tzOffset = firstDate.getTimezoneOffset()
        let secondDate = new Date(firstDate.setMinutes(firstDate.getMinutes() + tzOffset + (7*24*60)))

        let secondWeek = []

        weeklyData.forEach(function(d) {
            let newData = {
                "date": d3.timeFormat('%Y-%m-%d')(secondDate),
                // "datetime": d.datetime + 7,
                "detections": "N/A",
                "detections_text": "N/A",
                "percentpositive": "N/A",
                "percentpositive_text": "N/A",
                "province": d.province,
                "region": d.region,
                "tests": "N/A",
                "tests_text": "N/A",
                "virus": d.virus,
                "week": d.week + 1,
                "weekorder": d.weekorder + 1,
                "year": d.year
            }
            secondWeek = secondWeek.concat(newData)
        })
        weeklyData = weeklyData.concat(secondWeek)
    }

    // Group filtered data based on reporting lab and season
    let data = d3.group(weeklyData, d => d.province, d => d.date);

    lgG.append("text").attr("class", "fig1hoverText")
        .attr("transform", "translate(" + lgWidth * 0.42 + ", " + 10 + ")");

    d3.select("#fig1measureDD")
        .style("text-overflow", "ellipsis")
        .selectAll("option")
        .data(Object.keys(measuresList), d => d)
        .enter()
        .append("option")
        .attr("value", d => measuresList[d])
        // .text(d => d);
        .text(d => {
            if (d.includes("(%)")) {
                return eft(d.replace("Percent", "Percentage").replace(" (%)", ""))
            } else {
                return eft(d)
            }
        });

    d3.select("#fig1measureDD").on("change", function(d) {
        selectedMeasure = d3.select("#fig1measureDD").property("value");
        lgUpdate();
        // updateTable();
    });

    //might be region instead
    let dropdownOption = ["virus", "date"];
    // let selectedViruses = ["SARS-CoV-2","Influenza","RSV"]; // Changing these defaults, with the merging of RVDSS and CRVSR
    let selectedViruses = ["EV/RV","HPIV","ADV","HMPV","HCoV"];
    dropdownOption.forEach(function(dropdownName) {
        Array.from(d3.group(weeklyData, d => d[dropdownName])).forEach(function(d) {
                d3.select("#fig1" + dropdownName + "DD")
                    .append("option")
                    .attr("id", "fig1VB_" + d[0].replace(" ", "_").replace("/", "-"))
                    .attr("value", d[0])
                    .text(eft(d[0]));
        })
    });

    d3.selectAll(".virus_btn")
        .on("click", (d) => {
            d = (d.target.value == undefined ? d.target.parentNode: d.target)

            if (!d.className.includes("selectedBtn")) {
                selectedViruses.push(d.value)
                d3.select(d).classed("selectedBtn", true);
                d3.select("#fig1VB_" + d.value.replace(" ", "_").replace("/", "-"))
                .style("background-color", ogVirusFormats[d.value].colour)
            }
            else {
                if (selectedViruses.length == 1)
                    return
                selectedViruses.splice(selectedViruses.indexOf(d.value), 1)
                d3.select(d).classed("selectedBtn", false)
                    .style("background-color", "#eaebed");
            }

            // Tie regular virus buttons to the "select/deselect all" button...
            if (selectedViruses.length == d3.selectAll(".virus_btn").nodes().map(btn => btn.value).length) {
                d3.select("#fig1SelectAllViruses")
                // .classed("selectedBtn", true)
                .html(eft("Deselect all"))
            } else {
                d3.select("#fig1SelectAllViruses")
                // .classed("selectedBtn", false)
                .html(eft("Select all"))
            }

            lgUpdate();
        });



    // Define new select all viruses button + interactivity
    d3.select("#fig1_virus_buttons_div")
    .append("button")
    .attr("class", "btn btn-default allSelectorBtn selectAll allSelectorBtn-"+language)
    .attr("id", "fig1SelectAllViruses")
    .attr("value", "fig1SelectAllViruses")
    // .style("width","99px")
    // .html(eft("Select all"))
    .html(eft("Deselect all"))
    .on("click", function() {
        // Select all buttons in the repo lab div
        let allVirusBtns = d3.select("#fig1_virus_buttons_div")
            // .selectAll(".btn")
            .selectAll(".virus_btn")

        let allVirusNames = allVirusBtns.nodes().map(btn => btn.value)

        // If option is for "Select all", then switch it and select all buttons; else, do vice-versa
        if (!d3.select(this).html().toLowerCase().includes(eft("deselect"))) {
            d3.select(this).html(eft("Deselect all"));

            // Loop through virus buttons to colour individually
            allVirusNames.forEach(function(virusName) {

                let virusBtn = d3.select("#fig1VB_" + virusName.replace(/\s+/g, "_").replace("/", "-"))

                virusBtn.classed("selectedBtn", true)
                virusBtn.style("background-color", ogVirusFormats[virusName].colour);
                // selectedViruses = ["SARS-CoV-2","Influenza","RSV","Influenza A","Influenza B","EV/RV","HPIV","Adenovirus","HMPV","HCoV"];
                // selectedViruses = ["SARS-CoV-2","Influenza","RSV","Influenza A","Influenza B","EV/RV","HPIV","ADV","HMPV","HCoV"];
                selectedViruses = ["EV/RV","HPIV","ADV","HMPV","HCoV"]; // Update for Fall 2025 (start of 2025-26 season)
            });
        }
        else {
            d3.select(this).html(eft("Select all"));

            allVirusBtns.classed("selectedBtn", false);

            allVirusBtns.style("background-color", "#eaebed");
            // d3.select("#fig1VB_SARS-CoV-2").style("background-color", ogVirusFormats["SARS-CoV-2"].colour).classed("selectedBtn", true);
            // // selectedViruses = ["SARS-CoV-2"];
            // selectedViruses = ["SARS-CoV-2"];
            d3.select("#fig1VB_HPIV").style("background-color", ogVirusFormats["HPIV"].colour).classed("selectedBtn", true);
            // selectedViruses = ["SARS-CoV-2"];
            selectedViruses = ["HPIV"];
        }


        // Update the vis appropriately
        lgUpdate();

    })
         let PRUIDtoPT = {
            "Newfoundland and Labrador":10,
            "Prince Edward Island":11,
            "Nova Scotia":12,
            "New Brunswick":13,
            "Quebec":24,
            "Ontario":35,
            "Manitoba":46,
            "Saskatchewan":47,
            "Alberta":48,
            "British Columbia":59,
            "Yukon":60,
            "Northwest Territories":61,
            "Nunavut":62,
            "Canada":1,
            "Atlantic":2,
            "Prairies":36,
            "Territories":59.9
        }


    //make canada the default value
    d3.select("#fig1provinceDD").property("value", "Canada");

    d3.select("#fig1provinceDD").on("change", function(d) {
        selectedProvince = d.target.value;
        //should change because looks bad

        if (selectedProvince == "Saskatchewan" ||
            selectedProvince == "Prince Edward Island" ||
            selectedProvince == "Nunavut" ||
            selectedProvince == "Nova Scotia" ||
            selectedProvince == "Northwest Territories" ||
            selectedProvince == "Newfoundland and Labrador" ||
            selectedProvince == "New Brunswick" ||
            selectedProvince == "Manitoba" ||
            selectedProvince == "Alberta" ||
            selectedProvince == "Yukon") {
                d3.selectAll(".notCov").attr('disabled','disabled').attr("class","btn btn-default virus_btn notCov").style("background-color","#eaebed");
                selectedViruses = ["SARS-CoV-2"];
            }
        else{
            d3.selectAll(".notCov").attr('disabled',null);
            // if(selectedProvince == "Quebec"){
            //     // d3.select("#fig1VB_EV-RV").attr("disabled","disabled").classed("selectedBtn", false).style("background-color","#eaebed");
            //     // selectedViruses.filter(d => d != "EV/RV");
            //     d3.select("#fig1VB_EV-RV").attr("disabled","disabled");
            //     selectedViruses.filter(d => {
            //         return d != "EV/RV";
            //     });
            // }else if(selectedProvince == "Territories"){
            if(selectedProvince == "Territories"){
                d3.select("#fig1VB_EV-RV").attr("disabled","disabled").style("background-color","#eaebed");
                d3.select("#fig1VB_HPIV").attr("disabled","disabled").style("background-color","#eaebed");
                d3.select("#fig1VB_HMPV").attr("disabled","disabled").style("background-color","#eaebed");
                d3.select("#fig1VB_HCoV").attr("disabled","disabled").style("background-color","#eaebed");
                d3.select("#fig1VB_ADV").attr("disabled","disabled").style("background-color","#eaebed");

                selectedViruses.filter(d => {

                    return d == "SARS-CoV-2" || d == "Influenza" || d == "Influenza A" || d == "Influenza B" || d == "RSV" || d == "ADV";
                });
            }
        }
        lgUpdate();
    });
    let selectedProvince = d3.select("#fig1provinceDD").property('value');
    let selectedMeasure = d3.select("#fig1measureDD").property("value");
    // let selectedSeason = "2023-2024";
    let selectedSeason = (+weeklyData[0].year).toString() + "-" + (+weeklyData[0].year + 1).toString();
    d3.selectAll(".currentSeason").text(selectedSeason)

    //INPUT MAX DATE AS TOTAL DATES

    // Obtain max and min dates
    let sliderMin = d3.min(baselineData, d => d.current_week_end_date);
    let sliderMinDate = d3.timeParse("%Y-%m-%d")(sliderMin);
    let sliderMax = d3.max(baselineData, d => d.current_week_end_date);
    let sliderMaxDate = d3.timeParse("%Y-%m-%d")(sliderMax);

    let currDataSelection = Array.from(data.get(selectedProvince)).map(d => {     // <><><> Another option for filtering below, if it helps to fix the issue
        if (new Date(d[0]) >= sliderMinDate && new Date(d[0]) <= sliderMaxDate) {
            return d[1].filter(d => selectedViruses.includes(d.virus));
        } else {
            return [];
        }
    }).flat(1);

    // let currDataSelection = weeklyData.filter(function(d) {
    //     // return d.date >= timeframeNiceShort[0] && d.date <= timeframeNiceShort[1] && d.province == selectedProvince && selectedViruses.includes(d.virus);
    //     return d.date >= sliderMin && d.date <= sliderMax && d.province == selectedProvince && selectedViruses.includes(d.virus);
    // })

//  let totalDates = [];
//     //Fill currDatSelection with total dates with more dates
//     while(totalDates.length != 26){
//         if(totalDates.length >= currDataSelection.length){
//             let currDate = (totalDates[totalDates.length - 1]);
//             console.log(currDate)
//             currDate.setDate(currDate.getDate() + 7);
//             totalDates.push(new Date(currDate));
//         }
//         else
//             totalDates.push(d3.timeParse("%Y-%m-%d")(currDataSelection[totalDates.length].date));
//     }

//     console.log(totalDates)
    // Add DD options for "from" and "to" dates connected to the slider...
    // Identify all dates in order to create the necessary dropdown options
    // let allDates = [...new Set(d3.map(weeklyData, d => d.date))]
    let allDates = [...new Set(d3.map(baselineData, d => d.current_week_end_date))]

    let allDates1 = Array.from(allDates),
        allDates2 = Array.from(allDates);
    let firstDate = allDates1.shift();
    allDates1.shift();
    let lastDate = allDates2.pop();
    allDates2.pop();


    // Define remaining necessary time variables!

    let ytdDates = [...new Set(d3.map(weeklyData, d => d.date))]

    let sliderStartDates;
    // if (ytdDates.length == allDates.length) {
        sliderStartDates = [sliderMinDate, sliderMaxDate] // Setting the default slider dates to always be the start and end of the entire season now!
    // } else {
    //     sliderStartDates = [sliderMinDate, d3.timeParse("%Y-%m-%d")(allDates.filter(d => d > d3.max(ytdDates))[0])]
    // }

        // console.log(allDates, firstDate, lastDate)
    // Add all appropriate dates to the from and to date dropdowns connected to the slider
    d3.select("#fig1_from_dates")
        .selectAll(".fig1FromDateOptions")
        // .data(allDates.filter(d => d < lastDate))
        .data(allDates.filter(d => d < d3.max(ytdDates)))
        .enter()
        .append("p")
        .attr("class", "fig1FromDateOptions")
        .attr("id", (d) => "fig1FromDateOptions_" + d)
        // .text((d) => d)
        .text(function(d) {
            return d + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == d)[0].current_year_week + ")"
        })

    d3.select("#fig1_to_dates")
        .selectAll(".fig1ToDateOptions")
        .data(allDates.filter(d => d > firstDate))
        .enter()
        .append("p")
        .attr("class", "fig1ToDateOptions")
        .attr("id", (d) => "fig1ToDateOptions_" + d)
        // .text((d) => d)
        .text(function(d) {
            return d + eft(" (week ")+ baselineData.filter(z => z.current_week_end_date == d)[0].current_year_week + ")"
        })
    //append extra date to make it complete season


    // Initialize selections for the slider dates, in the from and to dropdowns (summary for the details)
    d3.select("#fig1TimeframeDropdownFromSummary").text(allDates[0] + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == allDates[0])[0].current_year_week + ")");
    d3.select("#fig1TimeframeDropdownToSummary").text(d3.timeFormat("%Y-%m-%d")(sliderStartDates[1]) + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == d3.timeFormat("%Y-%m-%d")(sliderStartDates[1]))[0].current_year_week + ")");


    // Prepare the search bars in either date dropdown
    let searchTerm = "";
    d3.select("#fig1_from_dd_search").on("keyup", function(key) {

        searchTerm = d3.select("#fig1_from_dd_search").property("value").toLowerCase()

        // let filteredDates = allDates.filter(d => d.includes(searchTerm))

        allDates.forEach(function(dateOption) {

            let dateID = "#fig1FromDateOptions_" + dateOption

            let dateText = dateOption + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == dateOption)[0].current_year_week + ")"

            if (dateText.includes(searchTerm)) {
                d3.select(dateID).style("display", "block")
            }
            else if (searchTerm == "") {
                d3.select(dateID).style("display", "block")
            }
            else {
                d3.select(dateID).style("display", "none")
            }

        })

    })

    d3.select("#fig1_to_dd_search").on("keyup", function(key) {

        searchTerm = d3.select("#fig1_to_dd_search").property("value").toLowerCase()

        allDates.forEach(function(dateOption) {

            let dateID = "#fig1ToDateOptions_" + dateOption

            let dateText = dateOption + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == dateOption)[0].current_year_week + ")"

            if (dateText.includes(searchTerm)) {
                d3.select(dateID).style("display", "block")
            }
            else if (searchTerm == "") {
                d3.select(dateID).style("display", "block")
            }
            else {
                d3.select(dateID).style("display", "none")
            }

        })

    })

// console.log([sliderMinDate, sliderMaxDate], baselineData)
    // Create the actual slider, with all correct dates and ranges...
    let timeframeSlider = noUiSlider.create(document.querySelector('#fig1TimeframeSlider'), {
        // The start values
        start: sliderStartDates,

        // Connect the two handles
        connect: true,

        // Range between the minimum and maximum values
        range: {
            'min': sliderMinDate.getTime(),
            'max': sliderMaxDate.getTime()
        },

        pips: {     // Include pips
            mode: 'steps',
            // values: [0, 100],
            density: 100,
            filter: (value, type) => {
                let date = new Date(value)

                let val = baselineData.filter(z => z.current_week_end_date == formatDateShort(date) || z.current_week_end_date == formatDateShort2(date))[0].week

                if (val % 2 == 0) {
                // // if ((val % 2 == 0 && val != 34) || val == 33) {
                // if (val == 34) {
                //     return 2
                // } else if (val % 2 == 0) {
                    return 0
                } else {
                    return 1
                }
            },
            format: {
                to: value => {
                    const date = new Date(value);
                    // return formatDate(date, "long")
                    return baselineData.filter(z => z.current_week_end_date == formatDateShort(date) || z.current_week_end_date == formatDateShort2(date))[0].week
                },
                from: value => {
                    const date = new Date(value);
                    // return formatDate(date, "long")
                    return baselineData.filter(z => z.current_week_end_date == formatDateShort(date) || z.current_week_end_date == formatDateShort2(date))[0].week
                }
            },
            stepped: true
        },

        // step: 1,
        step: 7 * 24 * 60 * 60 * 1000, // One week in milliseconds
        margin: 1 * (7 * 24 * 60 * 60 * 1000) // Must always have two weeks between the sliders -- fixed!
    });

    // Set the "from" and "to" dates to start with:

    // Define interactivity of date selectors with slider
    d3.selectAll(".fig1FromDateOptions").on("click", function() {

        let selectedFromDateFull = d3.select(this).html()
        let selectedFromDate = d3.select(this).html().split(" (")[0]

        let selectedToDateFull = d3.select("#fig1TimeframeDropdownToSummary").html()
        let selectedToDate = d3.select("#fig1TimeframeDropdownToSummary").html().split(" (")[0]

        // Update the summary text
        d3.select("#fig1TimeframeDropdownFromSummary").text(selectedFromDateFull)

        // Close both dropdowns
        d3.selectAll(".fig1TimeframeDropdown").property("open", false)

        // Update the slider from value
        // timeframeSlider.updateOptions({ start: [sliderStart, sliderEnd] })
        timeframeSlider.updateOptions({ start: [d3.timeParse("%Y-%m-%d")(selectedFromDate), d3.timeParse("%Y-%m-%d")(selectedToDate)] })

        // Call the visualization update function
        lgUpdate();

    })

    d3.selectAll(".fig1ToDateOptions").on("click", function() {

        let selectedToDateFull = d3.select(this).html()
        let selectedToDate = d3.select(this).html().split(" (")[0]

        let selectedFromDateFull = d3.select("#fig1TimeframeDropdownFromSummary").html()
        let selectedFromDate = d3.select("#fig1TimeframeDropdownFromSummary").html().split(" (")[0]

        // Update the summary text
        d3.select("#fig1TimeframeDropdownToSummary").text(selectedToDateFull)

        // Close both dropdowns
        d3.selectAll(".fig1TimeframeDropdown").property("open", false)

        // Update the slider from value
        // timeframeSlider.updateOptions({ start: [sliderStart, sliderEnd] })
        timeframeSlider.updateOptions({ start: [d3.timeParse("%Y-%m-%d")(selectedFromDate), d3.timeParse("%Y-%m-%d")(selectedToDate)] })

        // Call the visualization update function
        lgUpdate();

    })

    timeframeSlider.on('change', function(values) {
        const [from, to] = values.map(value => new Date(Number(value)));
        // Set the "from" and "to" dates to start with:
        // sliderMinDate = from;
        // sliderMaxDate = to;
        // d3.select('#fig1TimeframeDates').select(".leftSliderValue").text(formatDate(from))
        // d3.select('#fig1TimeframeDates').select(".rightSliderValue").text(formatDate(to))

        // console.log(from, formatDateShort(from + 1), formatDateShort(from) +1, to, formatDateShort(to), baselineData.filter(z => z.current_week_end_date <= formatDateShort(from + 1) && z.current_week_end_date >= formatDateShort(from - 1)), baselineData.filter(z => z.current_week_end_date <= formatDateShort(to + 1) && z.current_week_end_date >= formatDateShort(to - 1)))

        // let dateFilterFrom = baselineData.filter(z => z.current_week_end_date <= formatDateShort(from + (3 * 24 * 60 * 60 * 1000)) && z.current_week_end_date >= formatDateShort(from - (3 * 24 * 60 * 60 * 1000)))
        // let dateFilterTo = baselineData.filter(z => z.current_week_end_date <= formatDateShort(to + 1) && z.current_week_end_date >= formatDateShort(to - 1))

        let closestFrom = firstDate,
            closestTo = lastDate;

        allDates.forEach(function(dt) {

            if (Math.abs(from - d3.timeParse("%Y-%m-%d")(dt)) < Math.abs(from - d3.timeParse("%Y-%m-%d")(closestFrom))) {
                closestFrom = dt
            }

            if (Math.abs(d3.timeParse("%Y-%m-%d")(dt) - to) < Math.abs(d3.timeParse("%Y-%m-%d")(closestTo) - to)) {
                closestTo = dt
            }

        })

        let closestFromDate = baselineData.filter(z => z.current_week_end_date == closestFrom)
        let closestToDate = baselineData.filter(z => z.current_week_end_date == closestTo)

        // console.log(from, closestFrom, closestFromDate, to, closestTo, closestToDate)

        let fromDateFull = closestFromDate[0].current_week_end_date + eft(" (week ") + closestFromDate[0].current_year_week + ")"
        let toDateFull = closestToDate[0].current_week_end_date + eft(" (week ") + closestToDate[0].current_year_week + ")"

        // Update the From and to dates accordingly
        // d3.select("#fig1TimeframeDropdownFromSummary").html(formatDateShort(from))
        // d3.select("#fig1TimeframeDropdownToSummary").html(formatDateShort(to))
        d3.select("#fig1TimeframeDropdownFromSummary").html(fromDateFull)
        d3.select("#fig1TimeframeDropdownToSummary").html(toDateFull)

        // Close both dropdowns
        d3.selectAll(".fig1TimeframeDropdown").property("open", false)

        lgUpdate();

    });



    // Create x scale
    let xScale = d3.scaleTime()
        // .domain([totalDates[0],totalDates[totalDates.length - 1]])
        .domain([sliderMinDate, sliderMaxDate])
        .range([0, lgWidth]);

    // // Create x axis, displaying the epi-week number instead of the date




    // New xScale attempt
    let covidData = baselineData.filter(d => d.virus == "SARS-CoV-2")

    let tickDict2 = {}
    covidData.forEach(function(d) {
        tickDict2[d.current_week_end_date] = { "week": d.week, "weekorder": d.weekorder }
    })

    let xAxisScale = d3.scaleLinear()
    // .domain([tickDict2[timeframeNiceShort[0]].weekorder, tickDict2[timeframeNiceShort[1]].weekorder])
    .domain([tickDict2[d3.timeFormat("%Y-%m-%d")(sliderMinDate)].weekorder, tickDict2[d3.timeFormat("%Y-%m-%d")(sliderMaxDate)].weekorder])
    .range([0, lgWidth])

    // New xAxis attempt
    // let baselineDataFiltered = baselineData.filter(d => d.current_week_end_date >= timeframeNiceShort[0] && d.current_week_end_date <= timeframeNiceShort[1] && d.virus == "SARS-CoV-2");
    let baselineDataFiltered = baselineData.filter(d => d.current_week_end_date >= d3.timeFormat("%Y-%m-%d")(sliderMinDate) && d.current_week_end_date <= d3.timeFormat("%Y-%m-%d")(sliderMaxDate) && d.virus == "SARS-CoV-2");
    let allWeeks = [...new Set(baselineDataFiltered.map(d => d.current_week_end_date))]
    let numWeeks = [...new Set(baselineDataFiltered.map(d => d.current_week_end_date))].length
    let numTicks;
    numWeeks >= 26 ? numTicks = 26 : numTicks = numWeeks;

    // let stepSize = Math.ceil(numWeeks / (numTicks - 1));
    let stepSize = numWeeks < 26 ? 1 : Math.ceil(numWeeks / (numTicks - 1));
    let tickValuesFinal = []
    let tickValuesList = []
    for (let i = 0; i < numWeeks; i += stepSize) {
        tickValuesFinal.push(tickDict2[allWeeks[i]])
        tickValuesList.push(tickDict2[allWeeks[i]].weekorder)
    }
    // console.log(tickValuesFinal, tickValuesList)

    let xAxis = d3.axisBottom(xAxisScale)
    .tickValues(tickValuesList)
    .tickFormat((d,i) => {
        if (tickValuesFinal[i].weekorder == d)
            return tickValuesFinal[i].week

    })

    // Append xAxis to the g element
    lgG.append("g")
        .attr("transform", "translate(0, " + lgHeight + ")")
        .attr("class", "xAxis")
        .call(xAxis)
        .append("text") // Create/add x axis label
        .attr("class", "labelText")
        .attr("id", "xAxisLabel")
        .attr("y", 55)
        .attr("x", (lgWidth) / 2)
        .attr("text-anchor", "middle")
        .attr("fill", "black")
        .style("font-size", "23px")
        .text(function() {
            return eft("Surveillance week")
        });


    // Create y Scale and y Axis, scaling with to the current max
    let yMax = 0;

    // Determine the max Y value for the selected content being displayed
    selectedViruses.forEach(function(d) {
        // let newYMax = d3.max(data.get(selectedRegion), d2 => +d2["PercentPositive_"+d])
        let newYMax = d3.max(currDataSelection, d2 => +d2["PercentPositive_" + d])

        // Update y Max if newly selected content has a higher max value
        if (+yMax < +newYMax)
            yMax = newYMax;
    });

    // Define Y scale based on the maximum y value
    let yScale = d3.scaleLinear()
        // .domain([0, (selectedViruses.length == 0 ? 10 : +yMax * 1.1)])
        .domain([0, ((selectedViruses.length == 0) || (yMax < 8) ? 10 : +yMax * 1.25)])
        .range([lgHeight, 0]);

    // Define yAxis
    let yAxis = d3.axisLeft(yScale)
    .tickFormat(d => {
            if (language == "en") {
                if (selectedMeasure == "percentpositive") {
                    return d
                } else {
                    return (d3.format(","))(d)
                }
            } else {
                if (selectedMeasure == "percentpositive") {
                    return d
                } else {
                    return (d3.format(","))(d).toString().replace(",", " ")
                }
            }
        })

    // Add y Axis and label
    lgG.append("g")
        .attr("class", "yAxis")
        .call(yAxis)
        .append("text") // Add y Axis label
        .attr("class", "labelText")
        .attr("id", "yAxisLabel")
        .attr("text-anchor", "middle")
        .attr("fill", "black")
        .style("transform", `rotate(-90deg)`)
        .style("font-size", "23px")
        .attr("y", -57)
        .attr("x", -lgHeight / 2)
        .text(eft("Percentage of tests positive (%)"));


    if (selectedViruses.length)

        // Create g element to hold the lines of the graph
        lgG.append("g")
        .attr("class", "fig1LineG");

        // Add rect background element be an invisible, but hoverable backdrop for interaction with the lines
        d3.select(".fig1LineG").append("rect")
            .attr("class", "fig1RectBG")
            .attr("width", lgWidth)
            .attr("height", lgHeight)
            .attr("tabindex", 0)

    // Call the update function to first display graph's lines
    lgUpdate()

    // Define linegraph drawing/updating function
    function lgUpdate() {

        // Re-create timeframe dropdown options so that only appropriate dates are available for selection on either handle...
        // Remove all previous dates from both lists
        d3.select("#fig1_from_dates")
            .selectAll(".fig1FromDateOptions")
            .remove()

        d3.select("#fig1_to_dates")
            .selectAll(".fig1ToDateOptions")
            .remove()

        let selectedTimeframeRange = timeframeSlider.get()

        let timeframeNice = selectedTimeframeRange.map(value => new Date(Number(value)))

        let timeframeNiceShort = timeframeNice.map(value => d3.timeFormat("%Y-%m-%d")(value))


        d3.select("#fig1_from_dates")
            .selectAll(".fig1FromDateOptions")
            .data(allDates.filter(d => d < timeframeNiceShort[1]))
            .enter()
            .append("p")
            .attr("class", "fig1FromDateOptions")
            .attr("id", (d) => "fig1FromDateOptions_" + d)
            .text(function(d) {
                return d + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == d)[0].current_year_week + ")"
            })

        d3.select("#fig1_to_dates")
            .selectAll(".fig1ToDateOptions")
            .data(allDates.filter(d => d > timeframeNiceShort[0]))
            .enter()
            .append("p")
            .attr("class", "fig1ToDateOptions")
            .attr("id", (d) => "fig1ToDateOptions_" + d)
            .text(function(d) {
                return d + eft(" (week ") + baselineData.filter(z => z.current_week_end_date == d)[0].current_year_week + ")"
            })

        // Redefine interactivity of the dropdown date options with the slider
        d3.selectAll(".fig1FromDateOptions").on("click", function() {

            let selectedFromDateFull = d3.select(this).html()
            let selectedFromDate = d3.select(this).html().split(" (")[0]

            let selectedToDateFull = d3.select("#fig1TimeframeDropdownToSummary").html()
            let selectedToDate = d3.select("#fig1TimeframeDropdownToSummary").html().split(" (")[0]

            // Update the summary text
            d3.select("#fig1TimeframeDropdownFromSummary").text(selectedFromDateFull)

            // Close both dropdowns
            d3.selectAll(".fig1TimeframeDropdown").property("open", false)

            // Update the slider from value
            // timeframeSlider.updateOptions({ start: [sliderStart, sliderEnd] })
            timeframeSlider.updateOptions({ start: [d3.timeParse("%Y-%m-%d")(selectedFromDate), d3.timeParse("%Y-%m-%d")(selectedToDate)] })

            // Call the visualization update function
            lgUpdate();

        })

        d3.selectAll(".fig1ToDateOptions").on("click", function() {

            let selectedToDateFull = d3.select(this).html()
            let selectedToDate = d3.select(this).html().split(" (")[0]

            let selectedFromDateFull = d3.select("#fig1TimeframeDropdownFromSummary").html()
            let selectedFromDate = d3.select("#fig1TimeframeDropdownFromSummary").html().split(" (")[0]

            // Update the summary text
            d3.select("#fig1TimeframeDropdownToSummary").text(selectedToDateFull)

            // Close both dropdowns
            d3.selectAll(".fig1TimeframeDropdown").property("open", false)

            // Update the slider from value
            // timeframeSlider.updateOptions({ start: [sliderStart, sliderEnd] })
            timeframeSlider.updateOptions({ start: [d3.timeParse("%Y-%m-%d")(selectedFromDate), d3.timeParse("%Y-%m-%d")(selectedToDate)] })

            // Call the visualization update function
            lgUpdate();

        })

        let sliderMaxDate = d3.timeParse("%Y-%m-%d")(timeframeNiceShort[1])
        let sliderMinDate = d3.timeParse("%Y-%m-%d")(timeframeNiceShort[0])

        let sliderMinDateDown1 = new Date(Number(selectedTimeframeRange[0] - 86400000))

        // console.log(
        //     // sliderMinDate, sliderMinDateDown1, sliderMaxDate, timeframeNice, selectedTimeframeRange
        //     data.get(selectedProvince))

        // currDataSelection = Array.from(data.get(selectedProvince)).map(d => {
        //     if (new Date(d[0]) >= sliderMinDateDown1 && new Date(d[0]) <= sliderMaxDate)
        //         return d[1].filter(d => selectedViruses.includes(d.virus));
        //     else
        //         return [];
        // }).flat(1);

        // Ensure you're working with good dates...
        let closestTimeframeFrom = firstDate,
            closestTimeframeTo = lastDate;

        allDates.forEach(function(dt) {

            if (Math.abs(timeframeNice[0] - d3.timeParse("%Y-%m-%d")(dt)) < Math.abs(timeframeNice[0] - d3.timeParse("%Y-%m-%d")(closestTimeframeFrom))) {
                closestTimeframeFrom = dt
            }

            if (Math.abs(d3.timeParse("%Y-%m-%d")(dt) - timeframeNice[1]) < Math.abs(d3.timeParse("%Y-%m-%d")(closestTimeframeTo) - timeframeNice[1])) {
                closestTimeframeTo = dt
            }

        })

        let closestTimeframeFromDate = baselineData.filter(z => z.current_week_end_date == closestTimeframeFrom)[0].current_week_end_date
        let closestTimeframeToDate = baselineData.filter(z => z.current_week_end_date == closestTimeframeTo)[0].current_week_end_date

        let currDataSelection = weeklyData.filter(function(d) {
            // return d.date >= timeframeNiceShort[0] && d.date <= timeframeNiceShort[1] && d.province == selectedProvince && selectedViruses.includes(d.virus);
            return d.date >= closestTimeframeFromDate && d.date <= closestTimeframeToDate && d.province == selectedProvince && selectedViruses.includes(d.virus);
        })

        // Suppress data for NWT as necessary
        if (selectedProvince == "Northwest Territories") {
            currDataSelection.forEach(function(d) {
                if (selectedMeasure == "percentpositive" && d.tests < 45) {
                    d.percentpositive = eft("Suppressed")
                    d.percentpositive_text = eft("Suppressed")
                }
            })
        }

        // // Suppress data for Quebec as necessary (with EV/RV)
        // if (selectedProvince == "Quebec") {
        //     currDataSelection.forEach(function(d) {
        //         if (d.virus == "EV/RV") {
        //             d.percentpositive = eft("Suppressed")
        //             d.percentpositive_text = eft("Suppressed")
        //         }
        //     })
        // }

        // Update y axis
        let yMax = 0;

        // Determine the max Y value for the selected content being displayed
        selectedViruses.forEach(function(d) {
            // let newYMax = d3.max(data.get(selectedRegion), d2 => +d2["PercentPositive_"+d])
            let newYMax = d3.max(currDataSelection, d2 => +d2[selectedMeasure])

            // Update y Max if newly selected content has a higher max value
            if (+yMax < +newYMax)
                yMax = newYMax;
        });

        // console.log(currDataSelection)

        // Define Y scale based on the maximum y value
        let yScale = d3.scaleLinear()
            // .domain([0, (selectedViruses.length == 0 ? 10 : +yMax * 1.1)])
            .domain([0, ((selectedViruses.length == 0) || (yMax < 8) ? 10 : +yMax * 1.25)])
            .range([lgHeight, 0]);

        // Redefine yAxis
        let yAxis = d3.axisLeft(yScale)
        .tickFormat(d => {
            if (language == "en") {
                if (selectedMeasure == "percentpositive") {
                    return d
                } else {
                    return (d3.format(","))(d)
                }
            } else {
                if (selectedMeasure == "percentpositive") {
                    return d
                } else {
                    return (d3.format(","))(d).toString().replace(",", " ")
                }
            }
        })

        // Transition y Axis smoothly
        lgG.select(".yAxis")
            .transition()
            .duration(500)
            .call(yAxis);

        d3.select("#yAxisLabel").text(function(d){
            if(selectedMeasure == "percentpositive")
                // return eft("Positive tests (%)");
                return eft("Percentage of tests positive (%)");
            else if(selectedMeasure == "detections")
                return eft("Number of detections");
            else
                return eft("Number of tests");
        })


        let xScale = d3.scaleTime()
            // .domain([sliderMinDate,sliderMaxDate])
            .domain(timeframeNice)
            .range([0, lgWidth]);

        let xAxisScale = d3.scaleLinear()
        // .domain([tickDict2[timeframeNiceShort[0]].weekorder, tickDict2[timeframeNiceShort[1]].weekorder])
        .domain([tickDict2[closestTimeframeFromDate].weekorder, tickDict2[closestTimeframeToDate].weekorder])
        .range([0, lgWidth])


        // New xAxis attempt
        baselineDataFiltered = baselineData.filter(d => d.current_week_end_date >= closestTimeframeFromDate && d.current_week_end_date <= closestTimeframeToDate && d.virus == "SARS-CoV-2");
        allWeeks = [...new Set(baselineDataFiltered.map(d => d.current_week_end_date))]
        numWeeks = [...new Set(baselineDataFiltered.map(d => d.current_week_end_date))].length
        // let numTicks;
        numWeeks >= 26 ? numTicks = 26 : numTicks = numWeeks;

        // stepSize = Math.ceil(numWeeks / (numTicks - 1));
        let stepSize = numWeeks < 26 ? 1 : Math.ceil(numWeeks / (numTicks - 1));
        tickValuesFinal = []
        tickValuesList = []
        for (let i = 0; i < numWeeks; i += stepSize) {
            tickValuesFinal.push(tickDict2[allWeeks[i]])
            tickValuesList.push(tickDict2[allWeeks[i]].weekorder)
        }
        // console.log(tickValuesFinal, tickValuesList)

        let xAxis = d3.axisBottom(xAxisScale)
        .tickValues(tickValuesList)
        .tickFormat((d,i) => {
            // console.log(d, i, tickValuesFinal[i])
            if (tickValuesFinal[i].weekorder == d)
                return tickValuesFinal[i].week

        })

        // Smoothly transition x axis
        lgG.select(".xAxis")
            .transition()
            .duration(500)
            .call(xAxis);


        let lineGenerator = d3.lineChunked()
                .x(function(d) { return xScale(d3.timeParse("%Y-%m-%d")(d.date)); })
                .y(function(d) { return yScale(+d[selectedMeasure]); })
                .curve(d3.curveLinear)
                .defined(function(d) { return !isNaN(d[selectedMeasure]); })
                .lineAttrs({
                "stroke-dasharray": (d, i) => ogVirusFormats[d[0].virus] == undefined  ? "0":ogVirusFormats[d[0].virus]["stroke-pattern"],
                 fill: 'none',
                 stroke: (d, i) => ogVirusFormats[d[0].virus] == undefined  ? "black":ogVirusFormats[d[0].virus]["colour"],
                 'stroke-width': 3,
                 'stroke-opacity': 1,
                 'storke-linecap':'round'
                })
                .gapAttrs({
                    "stroke-dasharray":"5 5"
                })
                .gapStyles({
                    "opacity":0.5
                })
                .pointAttrs({
                 r: "0"
                });


        // Set all virus lines to 0 for a copy of Canada, to be used as a default line for better transitions in each season
        let blanks = [];
        currDataSelection.forEach(d => {
            let tempObj = {
                "detections": 0,
                "detections_text": 0,
                "percentpositive": 0,
                "tests": 0,
                "date": d.date,
                // "week": d.week,
                // "weekorder": d.weekorder,
            };
            blanks.push(tempObj);
        });
        currDataSelection = d3.group(currDataSelection, d => d.virus);

        if (currDataSelection.size < 1) {

            d3.select(".fig1LineG")
            .transition()
            .duration(250)
            .style("opacity", 0)

            d3.select("#fig1").select("svg").select("g")
            .append("text")
            .attr("transform", `translate(${lgWidth * 0.5}, ${lgHeight * 0.4})`)
            .attr("class", "fig1NoLinesText")
            .style("text-anchor", "middle")
            .text(eft("No data available for the selected timeframe"))

        } else {

            d3.selectAll(".fig1NoLinesText").remove()

            d3.select(".fig1LineG")
            .transition()
            .duration(250)
            .style("opacity", 1)

            //Draw the lines, define updating and exiting behaviour
            d3.select(".fig1LineG")
                .selectAll(".fig1virusLine")
                .data(selectedViruses)
                .join(
                    enter => enter.append("g")
                    .attr("class", d => "fig1virusLine fig1virusLine_" + d.replace("/", "-").replace(" ", "-"))
                    .attr("id", d => "fig1virusLine_" + d.replace("/", "-").replace(" ", "-"))
                    .datum(blanks)
                    .call(lineGenerator)
                    .datum((d, i) => currDataSelection.get(Array.from(currDataSelection.keys())[i]))
                    .transition().duration(700)
                    .call(lineGenerator),

                    update => update
                        .attr("class", d => {
                            return "fig1virusLine fig1virusLine_" + d.replace("/", "-").replace(" ", "-");
                        })
                        .attr("id", d => "fig1virusLine_" + d.replace("/", "-").replace(" ", "-"))
                        .datum((d,i) => currDataSelection.get(Array.from(currDataSelection.keys())[i]))
                        .transition()
                        .duration(700)
                        .call(lineGenerator)

                    ,
                    exit =>
                    exit.datum(blanks)
                        .transition().duration(700)
                        .call(lineGenerator)
                        .style("opacity", 0)
                        .remove()
                );

        }

        // Loop through all viruses to redetermine their colouring in the legend
        Object.keys(ogVirusFormats).forEach(function(d, i) {

            // If a virus is present in the graph, colour it appropriately; else, grey
            if (selectedViruses.indexOf(d) > -1) {
                d3.select("#legendLine" + d.replace("/", "-")).style("stroke", ogVirusFormats[d]["colour"]);

                d3.select("#legendText" + d.replace("/", "-")).attr("fill", "black");
            }
            else {
                d3.select("#legendLine" + d.replace("/", "-")).style("stroke", "grey");

                d3.select("#legendText" + d.replace("/", "-")).attr("fill", "grey");
            }

        });


        // Isolate one virus' baselineData for ease...
        let baselineDataCovid = baselineData.filter(z => z.virus == "SARS-CoV-2")

        // Define data for hover...
        let fig1hoverData = weeklyData.filter(function(d) {
            // return d.date >= timeframeNiceShort[0] && d.date <= timeframeNiceShort[1] && d.province == selectedProvince && selectedViruses.includes(d.virus);
            return d.date >= closestTimeframeFromDate && d.date <= closestTimeframeToDate && d.province == selectedProvince && selectedViruses.includes(d.virus);
        })

        // Suppress data for NWT as necessary
        if (selectedProvince == "Northwest Territories") {
            fig1hoverData.forEach(function(d) {
                if (selectedMeasure == "percentpositive" && d.tests < 45) {
                    d.percentpositive = eft("Suppressed")
                    d.percentpositive_text = eft("Suppressed")
                }
            })
        }

        // Define hover interactivity -- hover over whole graph area
        d3.selectAll(".fig1RectBG")
        .on('mouseover', function(event) {

            document.activeElement.blur()

            let xScale = d3.scaleTime()
                .domain(timeframeNice)
                .range([0, lgWidth]);

            // Get mouse position relative to graph area
            let [mouseX, mouseY] = d3.pointer(event)

            let closestPoint;

            // Get xValue at mouse position, converting it to YYYY-MM-DD format
            // const xValue = formatDate(xScale.invert(mouseX), "short")
            const xValue = d3.timeFormat("%Y-%m-%d")(xScale.invert(mouseX))

            // let yMax = d3.max(smData.get(repoLab), d2 => +d2[selectedMeasure + hoveredVirus])
            let yMax = 0;

            // Determine the max Y value for the selected content being displayed
            selectedViruses.forEach(function(d) {
                // let newYMax = d3.max(dataGrouped.get(hoveredRegionName).get(d), d2 => +d2[selectedMeasure])
                let newYMax = d3.max(currDataSelection.get(d), d2 => +d2[selectedMeasure])

                // Update y Max if newly selected content has a higher max value
                if (+yMax < +newYMax)
                    yMax = newYMax;

            });

            // Find closest data point to mouse position
            let findClosestPoint = function(mouseX) {

                // Use loop to find closest point to xValue

                // Define closest point as the first datapoint for the relevant repoLab, to start
                // closestPoint = currDataSelection.get(hoveredVirus)[0]
                closestPoint = baselineDataCovid[0]

                // Define the initial minimum distance as the distance of the current x value to the first data point's x value (parsing both dates to allow comparison)
                // let minDistance = Math.abs(d3.timeParse('%Y-%m-%d')(xValue) - d3.timeParse('%Y-%m-%d')(closestPoint.date))
                let minDistance = Math.abs(d3.timeParse('%Y-%m-%d')(xValue) - d3.timeParse('%Y-%m-%d')(closestPoint.current_week_end_date))

                // Loop through all data points for the relevant region
                baselineDataCovid.forEach(function(d) {

                    // Calculate the distance between the mouse position's x value and the current data point's x value (parsing both dates to allow comparison)
                    // let distance = Math.abs(d3.timeParse('%Y-%m-%d')(xValue) - d3.timeParse('%Y-%m-%d')(d.date))
                    let distance = Math.abs(d3.timeParse('%Y-%m-%d')(xValue) - d3.timeParse('%Y-%m-%d')(d.current_week_end_date))

                    // If the distance is less than the current minimum distance, then update the minimum distance and closest point to the current data point
                    if (distance < minDistance) {
                        minDistance = distance;
                        closestPoint = d;
                    }

                })

                return closestPoint

            }

            findClosestPoint(mouseX)


            // Retrieve week from hover
            let weekHovered = closestPoint.weekorder
            let epiWeekHovered = closestPoint.week
            let epiWeekDateHovered = closestPoint.current_week_end_date

            // Create full map of values to show on hover
            let hoveredInfo = {},
                hoveredInfoNew;

            // Filter data for relevant week
            let baselineWeekData = baselineDataCovid.filter(d => d.weekorder == weekHovered)
            let weeklyWeekData = fig1hoverData.filter(d => d.weekorder == weekHovered)

            // Determine which postpandemic seasons to include in hover information
            selectedViruses.forEach(function(virus) {
                let weeklyWeekVirusData = weeklyWeekData.filter(d => d.virus == virus)

                if (weeklyWeekVirusData.length > 0) {

                    let hoveredVal;
                    if (isNaN(weeklyWeekVirusData[0][selectedMeasure])) {
                        if (weeklyWeekVirusData[0][selectedMeasure+"_text"].toString() == "NaN") {
                            hoveredVal = eft("N/A")
                        } else {
                            hoveredVal = weeklyWeekVirusData[0][selectedMeasure+"_text"]
                        }
                    } else {
                        if (selectedMeasure == "percentpositive") {
                            if (language == "en") {
                                hoveredVal = parseFloat(weeklyWeekVirusData[0][selectedMeasure]).toFixed(1) + eft("%")
                            } else {
                                hoveredVal = parseFloat(weeklyWeekVirusData[0][selectedMeasure]).toFixed(1).toString().replace(".", ",") + eft("%")
                            }
                        } else {
                            if (language == "en") {
                                hoveredVal = d3.format(",")(weeklyWeekVirusData[0][selectedMeasure])
                            } else {
                                hoveredVal = d3.format(",")(weeklyWeekVirusData[0][selectedMeasure]).toString().replace(",", " ")
                            }
                        }
                    }

                    hoveredInfoNew = {
                        ...hoveredInfo,
                        [virus]: hoveredVal,
                    }
                    hoveredInfo = hoveredInfoNew;


                    // Show data dots for existing data points
                    if (!isNaN(weeklyWeekVirusData[0][selectedMeasure])) {
                        let cx = xScale(d3.timeParse('%Y-%m-%d')(weeklyWeekVirusData[0].date))
                        let cy = yScale(weeklyWeekVirusData[0][selectedMeasure])

                        d3.select(".fig1LineG")
                        .append("circle")
                        .attr("class", "fig1HoverDots")
                        .attr("id", "fig1HoverDots_"+virus.replace("/", "_"))
                        .attr("cx", cx)
                        .attr("cy", cy)
                        .attr("r", 5)
                        .attr("fill", ogVirusFormats[virus].colour)
                    } else {
                        d3.select("#fig1HoverDots_"+virus.replace("/", "_"))
                        .remove()
                    }

                }

            })

            // console.log(weeklyWeekData, baselineWeekData, hoveredInfo)

            // Manage the tooltip object that displays information for all seasons on the one hovered graph!
            d3.select("#fig1Tooltip")
            .html(null)

            // Get location of mouse to position tooltip appropriately
            let absMouseX = event.pageX,
                absMouseY = event.pageY

            // console.log(hoveredInfo, Object.keys(hoveredInfo))

            // Check where mouse is in comparison to the right of the screen to ensure it will still show the box
            // if (window.innerWidth - absMouseX < 280) {
            if (window.innerWidth - absMouseX < 305) {
                d3.select("#fig1Tooltip")
                .style("opacity", 1)
                .style("display", "block")
                .style("position", "absolute")
                .style("right", 0 + "px")
                .style("top", absMouseY - 120 + "px")
            } else {
                d3.select("#fig1Tooltip")
                .style("opacity", 1)
                .style("display", "block")
                .style("position", "absolute")
                .style("left", absMouseX + 10 + "px")
                .style("top", absMouseY - 120 + "px")
            }


            d3.select("#fig1Tooltip")
            .append("p")
            .attr("class", "fig1TooltipTitle")
            .text(eft("Week ") + epiWeekHovered + ` (${epiWeekDateHovered})`)

            d3.select("#fig1Tooltip")
            .selectAll(".fig1TTLines")
            .data(Object.keys(hoveredInfo))
            .enter()
            .append("div")
            .html((d) => {
                return `<div class="ttSVGDiv"><svg preserveAspectRatio="xMinYMin meet" viewBox="0 0 10 5"><line x1="0" x2="10" y1="2" y2="2" style="stroke:${ogVirusFormats[d].colour};stroke-dasharray:${ogVirusFormats[d]["stroke-pattern"]};"></line></svg></div> <p class="fig1TTLines">${eft(d)}${eft(": ")}${hoveredInfo[d]}</p>`
            })


            // Get new location, for snapping the line appropriately
            let xLine = xScale(d3.timeParse('%Y-%m-%d')(closestPoint.current_week_end_date))

            // Create a vertical line to show the xlocation of the mouse, intersecting all lines
            d3.select(".fig1LineG")
            .append("line")
            .attr("id", "fig1HoverLine")
            // .attr("x1", mouseX)
            // .attr("x2", mouseX)
            .attr("x1", xLine)
            .attr("x2", xLine)
            .attr("y1", 0)
            .attr("y2", lgHeight)

        })
        .on("mousemove", function(event) {

            let xScale = d3.scaleTime()
                    .domain(timeframeNice)
                    .range([0, lgWidth]);

            // Get mouse position relative to graph area
            let [mouseX, mouseY] = d3.pointer(event)

            let closestPoint;

            // Get xValue at mouse position, converting it to YYYY-MM-DD format
            const xValue = d3.timeFormat("%Y-%m-%d")(xScale.invert(mouseX))

            let yMax = 0;

            // Determine the max Y value for the selected content being displayed
            selectedViruses.forEach(function(d) {
                let newYMax = d3.max(currDataSelection.get(d), d2 => +d2[selectedMeasure])

                // Update y Max if newly selected content has a higher max value
                if (+yMax < +newYMax)
                    yMax = newYMax;

            });

            // Find closest data point to mouse position
            let findClosestPoint = function(mouseX) {

                // Use loop to find closest point to xValue

                // Define closest point as the first datapoint for the relevant repoLab, to start
                // closestPoint = currDataSelection.get(hoveredVirus)[0]
                closestPoint = baselineDataCovid[0]

                // Define the initial minimum distance as the distance of the current x value to the first data point's x value (parsing both dates to allow comparison)
                let minDistance = Math.abs(d3.timeParse('%Y-%m-%d')(xValue) - d3.timeParse('%Y-%m-%d')(closestPoint.current_week_end_date))

                // Loop through all data points for the relevant region
                baselineDataCovid.forEach(function(d) {

                    // Calculate the distance between the mouse position's x value and the current data point's x value (parsing both dates to allow comparison)
                    let distance = Math.abs(d3.timeParse('%Y-%m-%d')(xValue) - d3.timeParse('%Y-%m-%d')(d.current_week_end_date))

                    // If the distance is less than the current minimum distance, then update the minimum distance and closest point to the current data point
                    if (distance < minDistance) {
                        minDistance = distance;
                        closestPoint = d;
                    }

                })

                return closestPoint

            }

            findClosestPoint(mouseX)

            // Retrieve week from hover
            let weekHovered = closestPoint.weekorder
            let epiWeekEndHovered = closestPoint.current_week_end_date;
            let epiWeekHovered = closestPoint.week

            // Create full map of values to show on hover
            let hoveredInfo = {},
                hoveredInfoNew;

            // Filter data for relevant week
            let baselineWeekData = baselineDataCovid.filter(d => d.weekorder == weekHovered)
            let weeklyWeekData = fig1hoverData.filter(d => d.weekorder == weekHovered)

            // Determine which postpandemic seasons to include in hover information
            selectedViruses.forEach(function(virus) {
                let weeklyWeekVirusData = weeklyWeekData.filter(d => d.virus == virus)

                if (weeklyWeekVirusData.length > 0) {

                    let hoveredVal;
                    if (isNaN(weeklyWeekVirusData[0][selectedMeasure])) {
                        if (weeklyWeekVirusData[0][selectedMeasure+"_text"].toString() == "NaN") {
                            hoveredVal = eft("N/A")
                        } else {
                            hoveredVal = weeklyWeekVirusData[0][selectedMeasure+"_text"]
                        }
                    } else {
                        if (selectedMeasure == "percentpositive") {
                            if (language == "en") {
                                hoveredVal = parseFloat(weeklyWeekVirusData[0][selectedMeasure]).toFixed(1) + eft("%")
                            } else {
                                hoveredVal = parseFloat(weeklyWeekVirusData[0][selectedMeasure]).toFixed(1).toString().replace(".", ",") + eft("%")
                            }
                        } else {
                            if (language == "en") {
                                hoveredVal = d3.format(",")(weeklyWeekVirusData[0][selectedMeasure])
                            } else {
                                hoveredVal = d3.format(",")(weeklyWeekVirusData[0][selectedMeasure]).toString().replace(",", " ")
                            }
                        }
                    }

                    hoveredInfoNew = {
                        ...hoveredInfo,
                        [virus]: hoveredVal,
                    }
                    hoveredInfo = hoveredInfoNew;


                    // Update dot positions
                    let cx = xScale(d3.timeParse('%Y-%m-%d')(weeklyWeekVirusData[0].date))
                    let cy = yScale(weeklyWeekVirusData[0][selectedMeasure])

                    if (!isNaN(weeklyWeekVirusData[0][selectedMeasure])) {
                        if (d3.selectAll(".fig1HoverDots")["_groups"][0].length < selectedViruses.length) {
                            d3.select(".fig1LineG")
                            .append("circle")
                            .attr("class", "fig1HoverDots")
                            .attr("id", "fig1HoverDots_"+virus.replace("/", "_"))
                            .attr("cx", cx)
                            .attr("cy", cy)
                            .attr("r", 5)
                            .attr("fill", ogVirusFormats[virus].colour)
                        } else {
                            d3.select("#fig1HoverDots_"+virus.replace("/", "_"))
                            // .transition()
                            // .duration(10)
                            .attr("cx", cx)
                            .attr("cy", cy)
                        }
                    } else {
                        d3.select("#fig1HoverDots_"+virus.replace("/", "_"))
                        .remove()
                    }



                }

            })

            // Manage the tooltip object that displays information for all seasons on the one hovered graph!
            d3.select("#fig1Tooltip")
            .html(null)

            // Get location of mouse to position tooltip appropriately
            let absMouseX = event.pageX,
                absMouseY = event.pageY


            // Check where mouse is in comparison to the right of the screen to ensure it will still show the box
            // if (window.innerWidth - absMouseX < 280) {
            if (window.innerWidth - absMouseX < 305) {
                d3.select("#fig1Tooltip")
                .style("opacity", 1)
                .style("display", "block")
                .style("position", "absolute")
                .style("right", 0 + "px")
                .style("top", absMouseY - 120 + "px")
            } else {
                d3.select("#fig1Tooltip")
                .style("opacity", 1)
                .style("display", "block")
                .style("position", "absolute")
                .style("left", absMouseX + 10 + "px")
                .style("top", absMouseY - 120 + "px")
            }


            d3.select("#fig1Tooltip")
            .append("p")
            .attr("class", "fig1TooltipTitle")
            .text(eft("Week ") + epiWeekHovered + ` (${epiWeekEndHovered})`)


            d3.select("#fig1Tooltip")
            .selectAll(".fig1TTLines")
            .data(Object.keys(hoveredInfo))
            .enter()
            // .append("p")
            // .attr("class", "fig3TTLines")
            // .text((d) => {
            //     return `${d}: ${hoveredInfo[d]}`
            // })
            .append("div")
            .html((d) => {
                return `<div class="ttSVGDiv"><svg preserveAspectRatio="xMinYMin meet" viewBox="0 0 10 5"><line x1="0" x2="10" y1="2" y2="2" style="stroke:${ogVirusFormats[d].colour};stroke-dasharray:${ogVirusFormats[d]["stroke-pattern"]};"></line></svg></div> <p class="fig1TTLines">${eft(d)}${eft(": ")}${hoveredInfo[d]}</p>`
            })

            // Get new location, for snapping the line appropriately
            let xLine = xScale(d3.timeParse('%Y-%m-%d')(closestPoint.current_week_end_date))

            // Move the over line
            d3.select("#fig1HoverLine")
            // .transition()
            // .duration(5)
            // .attr("x1", mouseX)
            // .attr("x2", mouseX)
            .attr("x1", xLine)
            .attr("x2", xLine)

        })
        .on("mouseout", function() {
            d3.select("#fig1Tooltip")
            .style("opacity", 0)
            .style("display", "none")
            .html(null)

            d3.select("#fig1HoverLine")
            .remove()

            // d3.selectAll(".fig1HoverDots")
            // .transition()
            // .duration(50)
            // .style("opacity", 0)
            // .on("end", () => {
                d3.selectAll(".fig1HoverDots").remove()
            // })
        })

        // Update the table...
        updateFig1Table()

    }



    // Function for filling/updating table
    function updateFig1Table() {

        // Determine what data has been selected in order to filter the dataset appropriately for entering into the table
        let selectedTimeframeRange = timeframeSlider.get()

        let timeframeNice = selectedTimeframeRange.map(value => new Date(Number(value)))

        let timeframeNiceShort = timeframeNice.map(value => d3.timeFormat("%Y-%m-%d")(value))

        // Ensure you're working with good dates...
        let closestTimeframeFrom = firstDate,
            closestTimeframeTo = lastDate;

        allDates.forEach(function(dt) {

            if (Math.abs(timeframeNice[0] - d3.timeParse("%Y-%m-%d")(dt)) < Math.abs(timeframeNice[0] - d3.timeParse("%Y-%m-%d")(closestTimeframeFrom))) {
                closestTimeframeFrom = dt
            }

            if (Math.abs(d3.timeParse("%Y-%m-%d")(dt) - timeframeNice[1]) < Math.abs(d3.timeParse("%Y-%m-%d")(closestTimeframeTo) - timeframeNice[1])) {
                closestTimeframeTo = dt
            }

        })

        let closestTimeframeFromDate = baselineData.filter(z => z.current_week_end_date == closestTimeframeFrom)[0].current_week_end_date
        let closestTimeframeToDate = baselineData.filter(z => z.current_week_end_date == closestTimeframeTo)[0].current_week_end_date

        let currDataSelection = weeklyData.filter(function(d) {
            // return d.date >= timeframeNiceShort[0] && d.date <= timeframeNiceShort[1] && d.province == selectedProvince && selectedViruses.includes(d.virus);
            return d.date >= closestTimeframeFromDate && d.date <= closestTimeframeToDate && d.province == selectedProvince && selectedViruses.includes(d.virus);
        })


        // Watch for the 1-week fix with dummy data for the second week
        if ([... new Set(weeklyData.map(d => d.weekorder))].length == 2) {
            if ([... new Set(weeklyData.filter(d => d.weekorder == d3.max(weeklyData, d => d.weekorder)).map(d => d.tests))].length == 1) {
                currDataSelection = currDataSelection.filter(d => d.weekorder == d3.min(currDataSelection, d => d.weekorder))
            }
        }


        // Suppress data for NWT as necessary
        if (selectedProvince == "Northwest Territories") {
            currDataSelection.forEach(function(d) {
                if (selectedMeasure == "percentpositive" && d.tests < 45) {
                    d.percentpositive = eft("Suppressed")
                    d.percentpositive_text = eft("Suppressed")
                }
            })
        }

        // Remove the table if it has been created before
        if (fig1TextDescRuns > 0) {
            // d3.select("#fig1TextDesc_wrapper").remove()
            d3.select("#fig1TextDesc").html(null)
        }

        // Create table itself for figure 1
        let fig1_table = d3.select("#fig1TextDesc")
        .append("table")
        // .attr("class", "wb-tables table table-striped table-hover")
        // .attr("id", "fig1_table")
        .attr("class", "tdTable hover stripe row-border order order-column compact")

        // Organize dataset for table
        const fig1TableData = currDataSelection.map(function(d) {
            return {
                [ENFRTranslate("Region")]: eft(d.province),
                [ENFRTranslate("Virus")]: d.virus,
                [ENFRTranslate("Surveillance week")]: d.week,
                [ENFRTranslate("Week end-date")]: d.date,
                [ENFRTranslate("Number of tests")]: isNaN(d.tests) ? d.tests_text : d3.format(",")(+d.tests),
                [ENFRTranslate("Number of detections")]: isNaN(d.detections) ? d.detections_text : d3.format(",")(+d.detections),
                [ENFRTranslate("Percent positivity (%)")]: isNaN(d.percentpositive) ? d.percentpositive_text : parseFloat(+d3.format(".2f")(+d.percentpositive)).toFixed(1),
            }
        })

        // Fill the table with the appropriate data
        fig1_table
        .append("thead")
        .append("tr")
        .selectAll("th")
        .data(Object.keys(fig1TableData[0]))
        .enter()
        .append("th")
        .attr("data-orderable", "true")
        .text(function(d) {return d});

        let tr = fig1_table
        .append("tbody")
        .selectAll("tr")
        .data(fig1TableData)
        .enter()
        .append("tr");

        // Create and populate all cells in all rows
        let td = tr.selectAll("td")
        .data(function (d) {
          return Object.values(d);
        })
        .enter()
        .append("td")
        // .attr("class", "text-right")
        .attr("class", function(d,i) {
            if (i < 2) {
                return "text-left"
            } else if (i==2) {
                return "text-center"
            } else {
                return "text-right"
            }
        })
        .html(function (d) {
        //   return d;
            if (d.toString() == "NaN") {
                return eft("N/A")
            } else if (d == "N/A") {
                return eft("N/A")
            } else if (language == "fr" && d.toString().includes(".")) {
                return d.replace(".", ",")
            } else if (language == "fr" && d == "Atlantique") {
                return "Région de l'" + d
            } else {
                if (language == "fr" && d.toString().includes(",")) {
                    return d.replace(",", " ")
                } else {
                    return eft(d)
                }
            }
        });

        // Add data-sort attribute to the value columns so they sort appropriately
        fig1_table.selectAll("tr")
        .selectAll("td:nth-child(5)")
        // .attr("data-sort", (d) => +d)
        .attr("data-sort", function(d) {

            // // if (isNaN(d) || isNaN(d.toString()) || d.toString() == "NaN" || d == "N/A") {
            // if (d.toString() == "NaN" || d == "N/A") {
            //     d = 0
            // // } else if (d.includes(",")) {
            // } else if (d.includes(",") || d.includes(" ")) {
            //     d = d.replace(",", "")
            // } else if (isNaN(+d)) {
            //     d = 0
            // }

            let sort = (d.replace(",", "").replace(" ", ""))

            if (isNaN(+sort) || sort.toString() == "NaN" || sort == eft("N/A")) {
                sort = 0
            } else {
                sort = +sort
            }

            // return +d
            return sort
        })

        fig1_table.selectAll("tr")
        .selectAll("td:nth-child(6)")
        .attr("data-sort", function(d) {
            // // if (isNaN(d) || isNaN(d.toString()) || d.toString() == "NaN" || d == "N/A") {
            // if (d.toString() == "NaN" || d == "N/A") {
            //     d = 0
            // } else if (d.includes(",")) {
            //     d = d.replace(",", "")
            // } else if (isNaN(+d)) {
            //     d = 0
            // }
            // return +d

            let sort = (d.replace(",", "").replace(" ", ""))

            if (isNaN(+sort) || sort.toString() == "NaN" || sort == eft("N/A")) {
                sort = 0
            } else {
                sort = +sort
            }

            // return +d
            return sort
        })

        fig1_table.selectAll("tr")
        .selectAll("td:nth-child(7)")
        .attr("data-sort", function(d) {
            // if (isNaN(d) || isNaN(d.toString()) || d.toString() == "NaN" || d == "N/A") {
            if (d.toString() == "NaN" || d == "N/A") {
                d = 0
            } else if (d.includes(",")) {
                d = d.replace(",", "")
            } else if (isNaN(+d)) {
                d = 0
            }

            return +d
        })


        fig1TextDescRuns = fig1TextDescRuns + 1


        // Ensure table updates for proper render
        // $(".wb-tables").trigger("wb-init.wb-tables");



        // Try a new more explicit Data Table,
        if (language == 'en') {
            $(fig1_table.node()).DataTable();
        }
        else {
            $(fig1_table.node()).DataTable({
                "language": {
                    "sProcessing": "Traitement en cours...",
                    "sSearch": "Rechercher&nbsp;:",
                    "sLengthMenu": "Afficher _MENU_ &eacute;l&eacute;ments",
                    "sInfo": "Affichage de l'&eacute;leacute;ment _START_ &agrave; _END_ sur _TOTAL_ &eacute;l&eacute;ments",
                    "sInfoEmpty": "Affichage de l'&eacute;leacute;ment 0 &agrave; 0 sur 0 &eacute;l&eacute;ments",
                    "sInfoFiltered": "(filtr&eacute; de _MAX_ &eacute;l&eacute;ments au total)",
                    "sInfoPostFix": "",
                    "sLoadingRecords": "Chargement en cours...",
                    "sZeroRecords": "Aucun &eacute;l&eacute;ment &agrave; afficher",
                    "sEmptyTable": "Aucune donn&eacute;e disponible dans le tableau",
                    "oPaginate": {
                        "sFirst": "Premier",
                        "sPrevious": "Pr&eacute;c&eacute;dent",
                        "sNext": "Suivant",
                        "sLast": "Dernier"
                    },
                    "oAria": {
                        "sSortAscending": ": activer pour trier la colonne par ordre croissant",
                        "sSortDescending": ": activer pour trier la colonne par ordre d&eacute;croissant"
                    }
                },
            });
        }


        // Improve spacing of table headers...
        fig1_table.select("thead")
        .selectAll("th:first-child")
        .style("width", "175px")

        fig1_table.select("thead")
        .selectAll("th:nth-child(2)")
        .style("width", "80px")

        fig1_table.select("thead")
        .selectAll("th:nth-child(3)")
        .style("width", "30px")

        fig1_table.select("thead")
        .selectAll("th:nth-child(4)")
        // .style("width", "70px")
        .style("width", "80px")

        fig1_table.select("thead")
        .selectAll("th:nth-child(5)")
        .style("width", "50px")

        fig1_table.select("thead")
        .selectAll("th:nth-child(6)")
        .style("width", "50px")

        fig1_table.select("thead")
        .selectAll("th:nth-child(7)")
        .style("width", "50px")


    }


    // Run the table function for the first time
    updateFig1Table();


} // End of lgSetup function



// Format dates appropriately (full month name, then year)
function formatDate(date) {

    let dateDate1 = new Date(date)

    // Fix timezone differences...
    let tzOffset = dateDate1.getTimezoneOffset() // (in mins)
    let dateDate = new Date(dateDate1.setMinutes(dateDate1.getMinutes() + tzOffset))

    let monthNames;

    if (language == "en") {
        monthNames = ["January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ];
    }
    else {
        monthNames = ["janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"
        ];
    }

    // let day = dateDate.getDate();
    let day = dateDate.getDate();
    // let monthIndex = dateDate.getMonth() + 1;
    let monthIndex = dateDate.getMonth();
    let year = dateDate.getFullYear();
    // return day + ' ' + monthNames[monthIndex] + ', ' + year;
    if (language == "en") {
        return monthNames[monthIndex] + ' ' + day + ', ' + year;
    } else {
        // return day + ' ' + monthNames[monthIndex] + ', ' + year;
        return day + ' ' + monthNames[monthIndex] + ' ' + year;
    }
}

// Format dates appropriately (year-month-day)
function formatDateYMD(date) {

    let dateDate1 = new Date(date)

    // Fix timezone differences...
    let tzOffset = dateDate1.getTimezoneOffset() // (in mins)
    let dateDate = new Date(dateDate1.setMinutes(dateDate1.getMinutes() + tzOffset))

    // let day = dateDate.getDate() + 1;
    let day = dateDate.getDate();
    if (day < 10) {
        day = "0" + day
    }
    let month = dateDate.getMonth() + 1;
    // let month = dateDate.getMonth();
    if (month < 10) {
        month = "0" + month
    }
    let year = dateDate.getFullYear();

    // console.log(`${year}-${month}-${day}`)

    return `${year}-${month}-${day}`;
}


// Format dates appropriately (year-month-day)
function formatDateShort(date) {

    // let dateDate = new Date(date)

    let dateDate1 = new Date(date)

    // Fix timezone differences...
    let tzOffset = dateDate1.getTimezoneOffset() // (in mins)
    let dateDate = new Date(dateDate1.setMinutes(dateDate1.getMinutes() + tzOffset))

    let day = dateDate.getDate();
    if (day < 10) {
        day = "0" + day
    }
    let month = dateDate.getMonth() + 1;
    if (month < 10) {
        month = "0" + month
    }
    let year = dateDate.getFullYear();

    // console.log(`${year}-${month}-${day}`)

    return `${year}-${month}-${day}`;
}
// Format dates appropriately (year-month-day)
function formatDateShort2(date) {

    // let dateDate = new Date(date)
    let dateDate1 = new Date(date)

    // Fix timezone differences...
    let tzOffset = dateDate1.getTimezoneOffset() // (in mins)
    let dateDate = new Date(dateDate1.setMinutes(dateDate1.getMinutes() + tzOffset))

    // let day = dateDate.getDate() + 1;
    let day = dateDate.getDate();
    if (day < 10) {
        day = "0" + day
    }
    let month = dateDate.getMonth() + 1;
    if (month < 10) {
        month = "0" + month
    }
    let year = dateDate.getFullYear();

    // console.log(`${year}-${month}-${day}`)

    return `${year}-${month}-${day}`;
}


// // Function for translating French/English text
function ENFRTranslate(ogText) {

    let textLookupEN = {
        " total": "",
        "regional": "region",
        "viral": "virus",
        // "Health Status": "Health status",
        // "Social Factors": "Social factors",
    }

    let textLookupFR = {
        "Select all": "Sélectionner tout",
        "Deselect all": "Déselectionner tout",
        "deselect": "déselectionner",
        " (week ": " (semaine ",
        "Surveillance week": "Semaine de surveillance",
        "Percentage of tests positive (%)": "Pourcentage de tests positifs (%)",
        "Percent of tests positive (%)": "Pourcentage de tests positifs (%)",
        "Percentage of tests positive": "Pourcentage de tests positifs",
        "Positive tests (%)": "Tests positifs",
        "Number of detections": "Nombre de détections",
        "Number of tests": "Nombre de tests de dépistage",
        "No data available for the selected timeframe": "Aucune donnée disponible pour la période sélectionnée",
        "N/A": "S/O",
        // "N/A or suppressed":"S/O ou Supprimé",
        "N/A or suppressed":"S/O ou masqué",
        "Week ": "Semaine ",
        "; ": " ; ",
        ": ": " : ",
        "Region/Province": "Région/Province",
        "Region": "Région",
        "Virus": "Virus",
        "Week end-date": "Date de fin de la semaine",
        "Percent positivity (%)": "Pourcentage de positivité (%)",
        "percent positivity": "pourcentage de positivité",
        "Percent of tests positive": "Pourcentage de tests positifs",
        "Percentage of tests positive (%) for SARS-CoV-2": "Pourcentage de tests positifs (%) pour le SRAS-CoV-2",
        " or less": " ou moins",
        " or more": " ou plus",
        " to ": " à ",
        " for ": " pour ",
        // " for ": "",
        "percentage of ": "pourcentage de",
        "number of ": "nombre de ",
        "Pre-pandemic range": "Fourchette pré-pandémique",
        "Pre-pandemic average": "Moyenne pré-pandémique",
        "Data unavailable for selected season(s).": "Données indisponibles pour la ou les saisons sélectionnées.",
        "Current season percent positivity (%)": "Pourcentage de positivité de la saison en cours (%)",
        "2023-2024 percent positivity (%)": "Pourcentage de positivité 2023-2024 (%)",
        "2022-2023 percent positivity (%)": "Pourcentage de positivité 2022-2023 (%)",
        "2021-2022 percent positivity (%)": "Pourcentage de positivité 2021-2022 (%)",
        "2020-2021 percent positivity (%)": "Pourcentage de positivité 2020-2021 (%)",
        "Pre-pandemic average percent positivity (%)": "Pourcentage moyen de positivité pré-pandémique (%)",
        "Pre-pandemic range of percent positivity (%)": "Fourchette de pourcentage de positivité pré-pandémique (%)",
        "Reporting laboratory": "Laboratoire de déclaration",
        // "tested": "testés",
        "tested": "test",
        "positive": "positif",
        "detections": "détections",
        "Data N/A for": "Données N/D pour",
        "selected region": "la région sélectionnée",
        "selected region": "la période sélectionnée",
        "selected virus": "le virus sélectionné",
        "data N/A": "données N/D",
        "Territories": "Territoires",
        "Prairies": "Prairies",
        "Atlantic": "Atlantique",
        // "Suppressed": "Supprimé",
        "Suppressed": "Masqué",
        "Suppr.": "Mas.",
        // "Influenza (flu)": "Grippe",
        "Influenza": "Grippe",
        "Influenza": "Grippe",
        "SARS-CoV-2 (COVID-19)": "SRAS-CoV-2 (COVID-19)",
        "SARS-CoV-2": "SRAS-CoV-2",
        "RSV": "VRS",
        "HMPV": "MPVH",
        "HPIV": "VPIH",
        "Other HPIV": "Autre VPIH",
        "%": " %",
        "Total influenza" : "Grippe",
        "Total influenza A " : "Grippe A ",
        "Total influenza B " : "Grippe B ",
        "Influenza A" : "Grippe A",
        "Influenza B" : "Grippe B",
        "current week": "semaine en cours",
        "selected week": "semaine sélectionnée",
        "season to date": "saison à ce jour",
        "province and territory": "province et territoire",
        "reporting laboratory": "laboratoire rapporteur",
        "region": "région",
        " (region ": " (région ",
        "Atlantic": "Atlantique",
        "Quebec": "Québec",
        "British Columbia": "Colombie-Britannique",
        "Territories": "Territoires",
        "Newfoundland and Labrador": "Terre-Neuve-et-Labrador",
        "Prince Edward Island": "Île-du-Prince-Édouard",
        "Nova Scotia": "Nouvelle-Écosse",
        "New Brunswick": "Nouveau-Brunswick",
        "Northwest Territories": "Territoires du Nord-Ouest",
        "PHOL - Ottawa": "SPOL - Ottawa",
        "EORLA": "ALREO",
        "PHOL - Kingston": "SPOL - Kingston",
        "PHOL - Toronto": "SPOL - Toronto",
        "PHOL - Sault Ste. Marie": "SPOL - Sault Ste. Marie",
        "PHOL - Timmins": "SPOL - Timmins",
        "PHOL - London": "SPOL - London",
        "PHOL - Orillia": "SPOL - Orillia",
        "PHOL - Thunder Bay": "SPOL - Thunder Bay",
        "PHOL - Sudbury": "SPOL - Sudbury",
        "PHOL - Hamilton": "SPOL - Hamilton",
        "PHOL - Peterborough": "SPOL - Peterborough",
        "fixed": "fixe",
        "dynamic": "dynamique",
        " and ": " et ",
        "and ": "et ",
        "A(UnS) ": "A(NonS) ",
        "regional": "région",
        "viral": "virus",
        "week": "semaine",
        "week ending": "la semaine se terminant le",

        "Yes": "Oui",
        "No": "Non",
        "number": "nombre",
        "Number": "Nombre",
        "Year": "Année",

    }
    if (language == "en") {
        var t = textLookupEN[ogText];
        if (typeof t !== 'undefined')
            return t
        return ogText
    }
    else {
        var t = textLookupFR[ogText];
        if (typeof t !== 'undefined')
            return t
        return ogText
    }

}


// Define function for replacing an English region with the French name AND its relevant articles
function FRRegionAndArticle(reg) {

    let textLookupEN = {
    }

    let textLookupFR = {
        // "Territories": "Région des Territoires",
        // "Prairies": "Région des Prairies",
        // "Atlantic": "Région de l'Atlantique",
        "Territories": "des Territoires",
        "Prairies": "des Prairies",
        "Atlantic": "de l'Atlantique",

    }
    if (language == "en") {
        var t = textLookupEN[reg];
        if (typeof t !== 'undefined')
            return t
        return reg
    }
    else {
        var t = textLookupFR[reg];
        if (typeof t !== 'undefined')
            return t
        return reg
    }

}



// function wrap(text, width) {
//     text.each(function () {
//         var text = d3.select(this),
//             words = text.text().split(/\s+/).reverse(),
//             word,
//             line = [],
//             lineNumber = 0,
//             lineHeight = 1.1, // ems
//             x = text.attr("x"),
//             y = text.attr("y"),
//             dy = 0, //parseFloat(text.attr("dy")),
//             tspan = text.text(null)
//                         .append("tspan")
//                         .attr("x", x)
//                         .attr("y", y)
//                         .attr("dy", dy + "em");
//         while (word = words.pop()) {
//             line.push(word);
//             tspan.text(line.join(" "));
//             if (tspan.node().getComputedTextLength() > width) {
//                 line.pop();
//                 tspan.text(line.join(" "));
//                 line = [word];
//                 tspan = text.append("tspan")
//                             .attr("x", x)
//                             .attr("y", y)
//                             .attr("dy", ++lineNumber * lineHeight + dy + "em")
//                             .text(word);
//             }
//         }
//     });
// }



