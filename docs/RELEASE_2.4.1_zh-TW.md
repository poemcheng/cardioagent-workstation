# CardioAgent 2.4.1：CAC 與本機模型同次驗收

本版補上原 CAC 模型、同一病例與六次本機工具規劃的可重現驗收入口。模型權重仍須由使用者提供；軟體串接、權重來源聲明、AMD 裝置執行與臨床效能分別記錄。

## 完成的改動

- 工具規劃器核對回應的 model 與指定 ID，且須有 response ID；不符即記錄規則接續，不能算成模型成功。
- 模型登錄支援 `--confidence` 與 `--nms-iou`，預設分別為 0.25、0.5。已登錄 manifest 的後處理參數優先；舊 manifest 沿用 config.json。輸出保存實際後處理值、權重與 manifest SHA256、前處理、requested provider、session providers 與推論旗標。
- 固定同分候選的輸入次序；剔除裁切後面積為零的框；拒絕 NaN 輸入及不合法閾值。更換 provider 時重新建立 session。
- 原六工具流程於執行完成、補件續跑及核准前核對影像／病例資訊；來源變更會阻擋交接。急性升級仍優先處理，不因舊來源變更而拒絕升級。
- 新增 `scripts/accept_model_chain.py`。隔離資料與帳號、明確輸入順序／授權、指定預期權重 SHA256；保存六工具輸出、模型 ID／response ID／usage、來源雜湊、完整事件鏈、可列印 HTML 與逐檔校驗。
- 驗收結果保留待人工覆核，不會自動核准、建立會診任務或簽署報告。公開 WebAssembly 版更新程式與快取版本；沒有加入本機模型權重。

## 目標機操作

安裝方式沿用 [Windows 安裝指南](AMD_AIPC_QUICKSTART_2.3.1.md)。使用新版本的完整 Release 附件及新資料夾，不覆蓋舊的資料與環境。Python 3.11 x64 與 Lemonade／相符後端需事先備妥。

1. 使用已授權、與訓練相符的 ONNX，登錄正確後處理設定。原稿 NMS 0.7 與現版預設 0.5 不同；請依原實驗設定明確指定並另行重評。NMS 閾值與評估配對 IoU 是不同參數。

```powershell
.\.venv\Scripts\python.exe scripts\register_model.py D:\models\st_cac.onnx `
  --sha256 <完整權重SHA256> --trained --provenance "訓練與匯出紀錄識別碼" `
  --confidence 0.25 --nms-iou 0.7
```

`--trained` 是操作者的來源聲明，並非平台驗證訓練成果。模型須為單一 float32 `[1,3,512,512]` 輸入與 float32 `[1,5,N]` 原始單類輸出，沒有內建 NMS。

2. 啟動 Lemonade、下載相符文字模型，設定並檢查：

```powershell
.\.venv\Scripts\python.exe scripts\aipc_preflight.py `
  --configure --require-llm --model Phi-4-mini-instruct-GGUF
```

3. 準備 CT PNG 的實際切片順序，以及本機 `target-context.json`（只接受四個欄位）。下例僅示範格式；操作人員必須依授權研究案例填寫，不能把範例當成病史。

```json
{"age":62,"acute":false,"history_complete":true,"consent":true}
```

PNG 須為 512×512、8-bit、與訓練相同的窗位；不做每張 min-max 正規化、縮放或 DICOM HU 轉換。至少三張、至多 64 張，每張至多 8 MiB、總計至多 40 MiB。中心索引以零起算；預設為中間切片。輸入順序由命令列提供，不依檔名重新排序。

```powershell
.\.venv\Scripts\python.exe scripts\accept_model_chain.py `
  --images D:\ct\previous.png D:\ct\center.png D:\ct\next.png `
  --center 1 --context D:\ct\target-context.json --authorized-input `
  --model Phi-4-mini-instruct-GGUF --expected-vision-sha256 <完整權重SHA256>
```

輸出預設為新的 `evidence/target_runs/chain-<UTC>-<識別碼>/`。也可用 `--output` 指定全新資料夾；既有輸出禁止覆寫。原圖與原檔名不匯出；背景、候選與來源雜湊仍屬授權研究證據，分享前自行核對內容。

## 如何判讀

| 檔案 | 內容 |
|---|---|
| `index.html` | 可列印的驗收結果與受阻原因 |
| `acceptance.json` | 軟體版本、來源程式 SHA256、要求範圍、逐項判定 |
| `vision-manifest.json` | 登錄權重、前處理、後處理與來源聲明 |
| `local-model.json` | 指定本機模型的 ID、recipe、下載狀態 |
| `run-evidence.json` | 同次病例來源、六工具、模型回應、候選、待覆核狀態與零任務 |
| `audit-events.json` | 本次隔離資料的完整事件鏈；可重算雜湊，未做外部錨定 |
| `requests.json` | 真實 Flask test-client 請求與 HTTP 狀態；不是瀏覽器操作 |
| `SHA256SUMS.txt` | 所有輸出檔案的校驗值 |

要求範圍通過需：預期權重相符、有訓練來源聲明、六工具完成、六筆同模型且互異的 response ID、完整且一致的 token usage、確實執行 ONNX、保存後處理設定、來源與模型未變更、事件鏈有效，且沒有建立交接任務。任何規則接續、缺少 ID／usage、權重不符或來源變更，都使嚴格驗收失敗，命令回傳 1。可用部分結果仍會保存。

`--synthetic` 可替代 `--images` 演練軟體串接，仍要求登錄權重與本機 LLM；合成輸入不會標為研究輸入驗收完成。模型缺失時保留受阻報告，不用假模型補位。

## 證據範圍

本版開發測試使用特製未訓練 ONNX 與 loopback HTTP 協定服務，確認真正 ORT 解碼及請求鏈；這些不是 CAC 臨床權重或實際 LLM。另保留沒有權重／模型時的真實受阻執行。測試數、環境與原始結果見 `evidence/model_chain_2.4.1/`。

開發端全套 227 項 Python 測試通過（新增 10 項），準備中心 7 項 DOM／實際 Flask transport 檢查及 7 項 renderer 邏輯檢查通過。Windows 3.11 安裝、封裝及驗收以本次 GitHub Actions 的實際結果另列；上述開發結果不是 Windows 或 AMD 實機成績。

`passed_requested_scope` 只代表指定的軟體契約通過；`research_input_chain_verified` 額外表示操作者提供授權匯入影像，而非合成模式。兩者都不表示病灶正確率、原 PyTorch 與 ONNX 等價、臨床省時或 AMD 加速已驗證。Provider 清單不能當作逐節點／NPU 執行證據。實機仍需附相同 response ID 的 runtime 裝置紀錄、記憶體與離線冷啟動測試。

既有 MRI 完整展示保留原始 2.4.0 執行版本與數值，未改標為本次驗收成果。
