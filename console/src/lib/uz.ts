// All Uzbek Latin copy for the operator console.
// Rules:
//  - Latin script only; no Cyrillic, no Russian loan-words where Uzbek exists
//  - o' = Ö sound (o'tkazish, o'rtacha, o'q)
//  - g' = Ğ sound (g'oya, g'isht)
//  - Never say "xavfsiz" (safe) or "o'tishi mumkin" (may pass freely)
//  - Operator is always the decision-maker; system advises only

import type {
  ThreatCategory,
  RiskBand,
  ScanState,
  DetectionJudgement,
  OperatorOutcome,
  ScanSubject,
  ImageModality,
  DetectionStatus,
} from "./types";

// ---------------------------------------------------------------------------
// App chrome
// ---------------------------------------------------------------------------
export const APP_TITLE     = "Rentgen nazorat tizimi";
export const APP_SUBTITLE  = "Bojxona X-ray yordamchi tizimi";
export const LANE_LABEL    = "Yo'lak";
export const OPERATOR_LABEL = "Operator";
export const LOGOUT        = "Chiqish";
export const LOADING       = "Yuklanmoqda…";
export const ERROR_GENERIC = "Xato yuz berdi. Qayta urinib ko'ring.";

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------
export const LOGIN_TITLE     = "Tizimga kirish";
export const LOGIN_USERNAME  = "Foydalanuvchi nomi";
export const LOGIN_PASSWORD  = "Parol";
export const LOGIN_SUBMIT    = "Kirish";
export const LOGIN_ERROR     = "Foydalanuvchi nomi yoki parol noto'g'ri.";

// ---------------------------------------------------------------------------
// Scan states
// ---------------------------------------------------------------------------
export const SCAN_STATE: Record<ScanState, string> = {
  pending:    "Kutilmoqda",
  analyzing:  "Tahlil qilinmoqda…",
  analyzed:   "Tahlil qilindi",
  verdicted:  "Xulosa tayyor",
  reviewing:  "Ko'rib chiqilmoqda",
  decided:    "Qaror qilindi",
  error:      "Xato",
};

// ---------------------------------------------------------------------------
// Risk bands — always phrased as advisory, never as clearance
// ---------------------------------------------------------------------------
export const RISK_BAND: Record<RiskBand, string> = {
  clear:  "Shubhali buyum aniqlanmadi",
  low:    "Past xavf darajasi",
  medium: "O'rtacha xavf darajasi",
  high:   "Yuqori xavf darajasi",
};

export const RISK_BAND_SHORT: Record<RiskBand, string> = {
  clear:  "Aniqlanmadi",
  low:    "Past",
  medium: "O'rtacha",
  high:   "Yuqori",
};

// ⚠ Explicit: clear is NOT a pass decision — system says no finding; operator decides
export const CLEAR_DISCLAIMER =
  "Tizim hech qanday shubhali buyum aniqlamadi. "
  + "Qaror operatorga tegishli va jurnalga yoziladi.";

// ---------------------------------------------------------------------------
// Threat categories
// ---------------------------------------------------------------------------
export const THREAT_CATEGORY: Record<ThreatCategory, string> = {
  narcotics:        "Giyohvand modda",
  firearm:          "O'qotar qurol",
  bladed_weapon:    "Pichoq / o'tkir buyum",
  explosive:        "Portlovchi modda",
  currency:         "Valyuta / pul",
  organic_anomaly:  "Organik anomaliya",
  metallic_anomaly: "Metall anomaliya",
  contraband_other: "Boshqa taqiqlangan buyum",
  unknown:          "Noma'lum buyum",
};

// ---------------------------------------------------------------------------
// Detection status
// ---------------------------------------------------------------------------
export const DETECTION_STATUS: Record<DetectionStatus, string> = {
  completed:             "Muvaffaqiyatli",
  completed_no_findings: "Topilma yo'q",
  failed:                "Detektor xatosi",
};

// ---------------------------------------------------------------------------
// Scan subjects & modality
// ---------------------------------------------------------------------------
export const SCAN_SUBJECT: Record<ScanSubject, string> = {
  vehicle:  "Avtomobil",
  cargo:    "Yuk",
  baggage:  "Bagaj",
  parcel:   "Pochta jo'natma",
  other:    "Boshqa",
};

export const IMAGE_MODALITY: Record<ImageModality, string> = {
  single_energy: "Yagona energiya",
  dual_energy:   "Ikki energiya",
  multi_view:    "Ko'p rakurs",
};

// ---------------------------------------------------------------------------
// Viewer
// ---------------------------------------------------------------------------
export const VIEWER_TITLE           = "Skanerlangan tasvir";
export const VIEWER_NO_IMAGE        = "Tasvir yuklanmadi";
export const VIEWER_LOADING         = "Tasvir yuklanmoqda…";
export const VIEWER_FRAME_SELECT    = "Kadr tanlash";
export const VIEWER_ZOOM_IN         = "Kattalashtirish";
export const VIEWER_ZOOM_OUT        = "Kichiklashtirish";
export const VIEWER_ZOOM_RESET      = "Asl o'lcham";
export const VIEWER_DRAW_MODE       = "Belgilash rejimi";
export const VIEWER_DRAW_HINT       = "Detektor o'tkazib yuborgan buyumni belgilang";
export const VIEWER_DRAW_CANCEL     = "Bekor qilish";
export const VIEWER_ANALYZING_OVERLAY = "Tahlil qilinmoqda…";

// ---------------------------------------------------------------------------
// Detection cards
// ---------------------------------------------------------------------------
export const DETECTIONS_TITLE     = "Aniqlangan buyumlar";
export const NO_DETECTIONS        = "Shubhali buyum aniqlanmadi";
export const DETECTION_SCORE      = "Detektor ishonchi";
export const VERDICT_CONFIDENCE   = "Tizim ishonchi";
export const LOCATION_LABEL       = "Joylashuv (px)";
export const SIZE_LABEL           = "O'lcham";
export const DETECTION_ATTRIBUTES = "Qo'shimcha ma'lumot";

// Judgement controls on each detection card
export const JUDGE_CONFIRM       = "Tasdiqlash";
export const JUDGE_REJECT        = "Rad etish";
export const JUDGE_RECLASSIFY    = "Qayta tasniflash";
export const JUDGE_LABEL: Record<DetectionJudgement, string> = {
  confirmed:    "Tasdiqlangan",
  rejected:     "Rad etilgan",
  reclassified: "Qayta tasniflangan",
  unreviewed:   "Ko'rilmagan",
};

// ---------------------------------------------------------------------------
// Verdict panel
// ---------------------------------------------------------------------------
export const VERDICT_TITLE           = "Tizim xulosasi";
export const VERDICT_SUMMARY_LABEL   = "Umumiy xulosa";
export const VERDICT_GENERATED_BY    = "Hosil qilgan model";
export const VERDICT_ADVISORY_NOTE   =
  "Bu xulosa faqat ma'lumotnoma uchun. Qaror operatorga tegishli.";
export const VERDICT_PENDING         = "Xulosa tayyorlanmoqda…";
export const VERDICT_UNAVAILABLE     = "Xulosa mavjud emas";

// ---------------------------------------------------------------------------
// Decision panel
// ---------------------------------------------------------------------------
export const DECISION_TITLE         = "Operator qarori";
export const DECISION_SUBTITLE      =
  "Siz qaror qilasiz. Har qanday qaror jurnalga yoziladi.";
export const DECISION_NOTE_LABEL    = "Izoh (ixtiyoriy)";
export const DECISION_NOTE_HINT     = "Topilgan buyumlar yoki boshqa kuzatuvlar…";
export const DECISION_SUBMIT        = "Qarorni saqlash";
export const DECISION_SUBMITTING    = "Saqlanmoqda…";
export const DECISION_ALREADY_MADE  = "Qaror allaqachon qilindi";

export const OUTCOME_LABEL: Record<OperatorOutcome, string> = {
  cleared:   "Operator qarori bilan o'tkazish",
  inspected: "Qo'lda tekshirishga yuborish",
  seized:    "Buyumni musodara qilish",
  escalated: "Yuqori instansiyaga yuborish",
};

export const OUTCOME_DESC: Record<OperatorOutcome, string> = {
  cleared:
    "Skaner natijasini ko'rib chiqqandan so'ng operator to'siqsiz o'tkazishga qaror qildi.",
  inspected:
    "Jismoniy tekshiruv uchun yuborildi.",
  seized:
    "Taqiqlangan buyum musodara qilindi.",
  escalated:
    "Qaror yuqori instansiyaga havola qilindi.",
};

// Confirmation prompts for irreversible outcomes
export const CONFIRM_CLEARED =
  "Hech qanday jismoniy tekshiruvsiz o'tkazmoqchimisiz? "
  + "Bu qaror jurnalga yoziladi.";
export const CONFIRM_SEIZED =
  "Buyumni musodara qilishni tasdiqlaysizmi? Bu qaror qaytarib bo'lmaydi.";
export const CONFIRM_YES    = "Ha, tasdiqlayman";
export const CONFIRM_NO     = "Bekor qilish";

// ---------------------------------------------------------------------------
// Missed-region annotation
// ---------------------------------------------------------------------------
export const MISSED_TITLE       = "O'tkazib yuborilgan buyumlar";
export const MISSED_ADD         = "Yangi belgi qo'shish";
export const MISSED_CATEGORY    = "Buyum turi";
export const MISSED_NOTE        = "Izoh";
export const MISSED_SAVE        = "Saqlash";
export const MISSED_DELETE      = "O'chirish";
export const MISSED_HINT        =
  "Detektor o'tkazib yuborgan shubhali joyni belgilang.";

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------
export const AUDIT_TITLE      = "Audit jurnali";
export const AUDIT_EMPTY      = "Hali yozuvlar yo'q";
export const AUDIT_TIME       = "Vaqt";
export const AUDIT_OPERATOR   = "Operator";
export const AUDIT_EVENT      = "Hodisa";
export const AUDIT_SCAN_ID    = "Skan ID";
export const AUDIT_VERIFY     = "Zanjirni tekshirish";
export const AUDIT_VALID      = "Jurnal haqiqiy";
export const AUDIT_TAMPERED   = "⚠ Jurnal buzilgan bo'lishi mumkin";

// ---------------------------------------------------------------------------
// Scan queue
// ---------------------------------------------------------------------------
export const QUEUE_TITLE        = "Skanlar";
export const QUEUE_EMPTY        = "Yangi skan yo'q";
export const QUEUE_REFRESH      = "Yangilash";
export const QUEUE_FILTER_ALL   = "Barchasi";
export const QUEUE_FILTER_OPEN  = "Ochiq";
export const QUEUE_FILTER_DONE  = "Arxiv";
export const CAPTURE_BUTTON     = "Kameradan olish";
export const CAPTURE_WORKING    = "Olinmoqda…";
export const CAPTURE_ERROR      = "Kameradan olishda xato yuz berdi.";
export const ARCHIVE_CONFIRM    = "Tasdiqlash";
export const ARCHIVE_REJECT     = "Rad etish";
export const ARCHIVE_WORKING    = "Saqlanmoqda…";
export const ARCHIVE_ERROR      = "Qarorni saqlashda xato yuz berdi.";
export const ARCHIVE_DONE       = "Skan arxivga tushdi.";

// ---------------------------------------------------------------------------
// Confidence meter
// ---------------------------------------------------------------------------
export const CONF_HIGH_LABEL   = "Yuqori ishonch";
export const CONF_MEDIUM_LABEL = "O'rtacha ishonch";
export const CONF_LOW_LABEL    = "Past ishonch";
export const CONF_NOTE_LOW     =
  "Past ishonch — bu natija ehtiyotkorlik bilan baholansin.";

// ---------------------------------------------------------------------------
// Accessibility / screen-reader only
// ---------------------------------------------------------------------------
export const SR_CLOSE           = "Yopish";
export const SR_SELECTED        = "Tanlangan";
export const SR_RISK_HIGH       = "Diqqat: yuqori xavf darajasi aniqlandi";
export const SR_NEW_SCAN        = "Yangi skan keldi";
export const SR_DECISION_LOGGED = "Qaror jurnalga yozildi";

// ---------------------------------------------------------------------------
// Connection status indicator
// ---------------------------------------------------------------------------
export const CONN_OPEN         = "Ulangan";
export const CONN_CONNECTING   = "Ulanmoqda…";
export const CONN_CLOSED       = "Aloqa uzildi";
export const CONN_CLOSED_HINT  =
  "Server bilan aloqa yo'q. Yangi skanlar ko'rinmasligi mumkin — qayta ulanmoqda.";

// ---------------------------------------------------------------------------
// Visible (non-screen-reader) error & queue-failure messages
// ---------------------------------------------------------------------------
export const QUEUE_LOAD_ERROR  = "Navbatni yuklashda xato — server bilan aloqa yo'q.";
export const SCAN_LOAD_ERROR   = "Skanni yuklashda xato yuz berdi.";
export const RETRY             = "Qayta urinish";

// ---------------------------------------------------------------------------
// High-risk persistent alert banner
// ---------------------------------------------------------------------------
export const HIGH_ALERT_TITLE   = "Diqqat: yuqori xavf darajasi aniqlandi";
export const HIGH_ALERT_BODY    = "Yangi yuqori xavfli skan keldi. Iltimos, ko'rib chiqing.";
export const HIGH_ALERT_OPEN    = "Skanni ochish";
export const HIGH_ALERT_DISMISS = "Ogohlantirishni yopish";
export const SOUND_ON           = "Tovushli signal yoqilgan";
export const SOUND_OFF          = "Tovushli signal o'chirilgan";

// ---------------------------------------------------------------------------
// Header "mark as reviewed" (non-decision) action
// ---------------------------------------------------------------------------
export const MARK_REVIEWED       = "Ko'rib chiqildi deb belgilash";
export const MARK_REVIEWED_DONE  = "Ko'rib chiqilgan deb belgilandi";

// ---------------------------------------------------------------------------
// Reinforced safety confirmation when clearing a HIGH-risk scan
// ---------------------------------------------------------------------------
export const CONFIRM_CLEARED_HIGH =
  "DIQQAT: tizim bu skanni YUQORI xavf deb baholadi, ammo siz uni "
  + "jismoniy tekshiruvsiz o'tkazmoqchisiz. Bu ziddiyatli qaror jurnalga "
  + "yoziladi. Davom etishni tasdiqlaysizmi?";
export const SEIZED_NOTE_REQUIRED =
  "Musodara qarori uchun izoh majburiy. Iltimos, sababini yozing.";

// ---------------------------------------------------------------------------
// Live camera
// ---------------------------------------------------------------------------
export const LIVE_TITLE          = "Jonli kamera";
export const LIVE_START          = "Oqimni boshlash";
export const LIVE_STOP           = "Oqimni to'xtatish";
export const LIVE_STARTING       = "Boshlanmoqda…";
export const LIVE_STOPPING       = "To'xtatilmoqda…";
export const LIVE_RUNNING        = "Oqim faol";
export const LIVE_STOPPED        = "Oqim to'xtatilgan";
export const LIVE_NO_SIGNAL      = "Video signal yo'q";
export const LIVE_DEVICE         = "Qurilma";
export const LIVE_CADENCE        = "Tahlil oralig'i";
export const LIVE_FRAMES         = "Tahlil qilingan kadrlar";
export const LIVE_LAST_ANALYSIS  = "Oxirgi tahlil";
export const LIVE_ANALYSIS_TITLE = "Jonli tahlil oqimi";
export const LIVE_NO_ANALYSIS    = "Hali tahlil natijasi yo'q";
export const LIVE_DETECTIONS     = "Aniqlangan buyumlar";
export const LIVE_ERROR          = "Kamera oqimida xato yuz berdi.";
