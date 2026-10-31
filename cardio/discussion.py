"""Source-backed Heart Team preparation; never an automatic procedure decision."""
import copy
import json
from pathlib import Path


FLAGS = (
    "acute", "anatomy_reviewed", "ischemia_reviewed", "lvef_reviewed",
    "risk_reviewed", "preferences_reviewed", "medication_reviewed",
)
REVIEW_FLAGS = FLAGS[1:]

LABELS = {
    "acute": "急性警訊與當下狀態",
    "anatomy_reviewed": "冠狀動脈解剖與病灶資料",
    "ischemia_reviewed": "症狀、缺血與功能評估",
    "lvef_reviewed": "左心室功能與相關檢查",
    "risk_reviewed": "手術／介入與共病風險",
    "preferences_reviewed": "病人目標、偏好與共享決策",
    "medication_reviewed": "既有治療與用藥資料",
}

DETAILS = {
    "acute": "由人員確認是否需優先啟動院所急症流程；系統不從候選框推斷急性病況。",
    "anatomy_reviewed": "由專業人員核對適用的冠狀動脈解剖與病灶證據；單純鈣化候選與閾值表面不能代替此項。",
    "ischemia_reviewed": "由專業人員核對症狀與適用的缺血／功能檢查，不由影像亮度或模型摘要判定。",
    "lvef_reviewed": "由人員核對正式左心室功能檢查；本版未從少量 MRI 切片自動測量 LVEF。",
    "risk_reviewed": "由團隊核對手術及介入風險、共病、衰弱與恢復需求；本版不計算 STS 或其他風險分數。",
    "preferences_reviewed": "確認已納入病人目標、偏好與可理解的替代方案說明；本清單不代替知情同意。",
    "medication_reviewed": "核對既有治療、用藥反應與限制；系統不建議個人化藥物、劑量或停藥。",
}

SOURCES_BY_FLAG = {
    "acute": ["aha-acc-scai-2021"],
    "anatomy_reviewed": ["aha-acc-scai-2021", "esc-ccs-2024"],
    "ischemia_reviewed": ["esc-ccs-2024"],
    "lvef_reviewed": ["aha-acc-scai-2021"],
    "risk_reviewed": ["aha-acc-scai-2021", "aha-older-adults-2025"],
    "preferences_reviewed": ["aha-acc-scai-2021", "aha-older-adults-2025"],
    "medication_reviewed": ["esc-ccs-2024"],
}


class Discussion:
    """Translate explicit human attestations into a discussion workflow state.

    `ready_for_mdt` means the six review flags were supplied as true and acute
    was explicitly false. It does not certify that the underlying evidence is
    sufficient, that an indication exists, or that a procedure is appropriate.
    The module performs no model call or external network access.
    """

    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[1])
        self.sources = json.loads((self.root / "assets/sop/clinical_sources.json").read_text(encoding="utf-8"))

    @staticmethod
    def validate_context(context):
        if not isinstance(context, dict):
            raise ValueError("術前討論內容須為 JSON 物件。")
        if set(context) - set(FLAGS) - {"clinician_note"}:
            raise ValueError("術前討論只接受已定義的覆核旗標與專業人員備註。")
        flags = {}
        for key in FLAGS:
            value = context.get(key)
            if value is not None and type(value) is not bool:
                raise ValueError(f"{LABELS[key]}須為 true、false 或 null。")
            flags[key] = value
        note = context.get("clinician_note", "")
        if not isinstance(note, str) or len(note) > 4000:
            raise ValueError("專業人員備註須為字串，且不超過 4000 字。")
        return flags, note.strip()

    @staticmethod
    def _nested(analysis, name):
        value = analysis.get(name, {})
        return value if isinstance(value, dict) else {}

    def assess(self, analysis, context):
        if not isinstance(analysis, dict):
            raise ValueError("請提供本次分析的結構化證據。")
        flags, note = self.validate_context(context)
        case = self._nested(analysis, "case")
        snapshot = self._nested(analysis, "snapshot")
        source = self._nested(analysis, "source")
        modality = analysis.get("modality") or case.get("modality") or snapshot.get("modality") or source.get("modality") or "UNKNOWN"
        synthetic = any(part.get("synthetic") is True for part in (analysis, case, snapshot, source))
        run_snapshot = self._nested(analysis, "run_snapshot")
        acute_in_evidence = run_snapshot.get("acute") is True
        acute_conflict = acute_in_evidence and flags["acute"] is False
        if acute_in_evidence:
            flags["acute"] = True
        missing = [key for key in FLAGS if flags[key] is None or (key in REVIEW_FLAGS and flags[key] is not True)]
        urgent = flags["acute"] is True
        status = "emergency" if urgent else "incomplete" if missing else "ready_for_mdt"
        checks = []
        for key in FLAGS:
            check_status = "urgent" if key == "acute" and urgent else "reviewed" if (flags[key] is False if key == "acute" else flags[key] is True) else "missing"
            checks.append({"key": key, "label": LABELS[key], "status": check_status,
                           "detail": DETAILS[key], "value": flags[key],
                           "origin": "existing_analysis_snapshot" if key == "acute" and acute_in_evidence else "human_attestation",
                           "source_ids": SOURCES_BY_FLAG[key]})
        limits = [
            "本清單記錄人員表示已覆核的項目，不驗證資料內容；完成勾選只表示可安排多專業討論。",
            "候選框、鈣化數量或零候選不能單獨確定狹窄、缺血、手術適應症或排除疾病。",
            "閾值分割／展示網格不是經驗證的冠狀動脈、手術切割面或器械導航模型；不能用於手術規劃。",
            "本版未建立病人層級治療選擇模型，不產生 PCI／CABG 排序、手術風險分數、藥物調整或手術處方。",
        ]
        if synthetic:
            limits.insert(0, "本次包含合成影像／體模，僅驗證軟體與幾何流程，不能形成任何病人的處置建議。")
        if modality != "CT":
            limits.append("本次影像為 " + str(modality) + "；不套用 CT 鈣化偵測適應症，也未由少量研究切片完成心功能評估。")
        options = [] if urgent else [
            {"id": "medical_review", "title": "既有治療與追蹤討論", "scope": "教育性方案類別，未套用至本次個案",
             "questions": ["既有治療與生活型態管理的目標、反應及限制是否已由臨床團隊確認？"],
             "source_ids": ["esc-ccs-2024"]},
            {"id": "pci_discussion", "title": "經皮冠狀動脈介入治療（PCI）討論", "scope": "教育性方案類別，未判定適應症",
             "questions": ["解剖與病灶可處理性、症狀／缺血關聯，以及治療相關風險是否已有專業評估？"],
             "source_ids": ["aha-acc-scai-2021", "esc-ccs-2024"]},
            {"id": "cabg_discussion", "title": "冠狀動脈繞道手術（CABG）討論", "scope": "教育性方案類別，未判定適應症",
             "questions": ["心臟外科是否已評估解剖複雜度、手術與共病風險、恢復需求、替代方案及病人目標？"],
             "source_ids": ["aha-acc-scai-2021", "aha-older-adults-2025"]},
        ]
        message = ("已標示急性警訊；優先交由人員依院所急症流程處理，不等待一般分析或手術討論清單。" if urgent else
                   "仍有討論資料未確認；請由專業人員補充或核對。" if missing else
                   "已提供全部覆核旗標，可交由 Heart Team 討論；這不是手術適應症、知情同意或手術核准。")
        return {
            "schema": "cardio-discussion/1", "status": status, "message": message,
            "missing": missing, "missing_labels": [LABELS[key] for key in missing],
            "acute_conflict": acute_conflict,
            "checks": checks, "context": flags, "options": options,
            "sources": copy.deepcopy(self.sources),
            "clinical_note": {"text": note, "origin": "clinician_entered", "verified": False},
            "evidence_snapshot": {"analysis_id": analysis.get("id"), "analysis_version": analysis.get("version"),
                                  "case_id": analysis.get("case_id"), "modality": modality, "synthetic": synthetic},
            "evidence_limits": limits,
            "recommendation": {"procedure": None, "reason": "處置選擇由合格的多專業團隊與病人依完整臨床證據共同決策。"},
            "surgery_planning_ready": False, "automatic_orders_created": False,
            "method": "source_backed_human_review_checklist", "model_used": False,
            "notice": "離線來源摘要已註明版本與查核日；它不是院內最新指引同步服務。",
        }
