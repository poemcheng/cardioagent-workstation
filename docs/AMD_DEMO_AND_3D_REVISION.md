# AMD competition demonstration and source-bound 3D revision

Revision: 2026-11-03. Public demo release 2.3.0.

## AMD is part of the main demonstration

The official AMD AI Agent category requires actual use of an eligible AMD resource during the final demonstration: AMD–ITRI Joint Lab, AMD cloud LLM, or AMD GPU / AMD AI PC. The listed examples are alternatives and resources, rather than a requirement to use every SDK. A CPU-only browser demonstration does not establish Ryzen AI or NPU execution.

Official category: https://innoserveawards.tca.org.tw/advertise26_AMD.htm

| Tool/resource | Role in CardioAgent | Evidence and current status |
|---|---|---|
| Lemonade Server / SDK | Local OpenAI-compatible model endpoint; constrained workflow planning and summaries | API adapter and local setup exist. Record actual model ID, response provenance and tool plan on the target PC. No target-PC model execution measured in this revision. |
| llama.cpp Vulkan through Lemonade | GGUF inference using a compatible AMD iGPU; target for the current local-model preset | Confirm actual recipe/backend and device on the PC. A loaded GGUF does not establish NPU use. |
| Ryzen AI Hybrid / OGA / FastFlowLM | Alternative NPU or hybrid inference paths when supported by OS, processor and model | Candidate validation path; not measured by this project. Check the current support matrix. |
| Ryzen AI CNN examples / YOLO + Quark | Export, quantize, calibrate and validate an eligible medical model for NPU inference | Example workflow only. COCO object-detector weights cannot establish cardiac detection performance. Current registered ONNX adapter uses CPUExecutionProvider. |
| AMD–ITRI Joint Lab / AUP learning cloud | Optional training, optimization and resource-learning routes | Not used by this revision; no allocation or training claim. |
| AMD AI Bundle | Optional environment installation route | Not the inference backend or evidence of acceleration. |

Official backend references:
- https://lemonade-server.ai/docs/guide/faq/
- https://ryzenai.docs.amd.com/en/latest/llm/overview.html
- https://github.com/amd/RyzenAI-SW/tree/main/CNN-examples/object_detection

### Competition demonstration sequence

1. Start the local workstation and Lemonade on the AMD AI PC. Show the hardware/OS and the actual loaded model/recipe/backend.
2. Run `scripts/collect_aipc_evidence.ps1` and keep the generated evidence. Use `scripts/aipc_preflight.py` / `scripts/evaluate_aipc.py` to inspect readiness and repeated requests.
3. Execute a workflow, show model versus rules provenance, tool dependencies, a missing-data pause and a resume, then human review and the resulting handoff.
4. Show the research MRI overlay and the source-bound 3D preview. These provided labels are not AI predictions; no self-comparison Dice is reported.
5. Present latency, failures and memory from that PC. The public browser fallback is independently usable but does not prove AMD model acceleration.

The present host reports AMD EPYC CPU, not the target Ryzen AI PC. This revision executed software and geometry checks, without Lemonade model weights, NPU execution, hardware speedup or clinical efficacy measurements.

## 3D correction and verification

The previous 8-slice MRI surface pooled a full 512 × 428 field to steps Z/Y/X = 1/8/7 and rendered flat face normals. With unknown physical spacing, an unscaled voxel display flattened the short-axis stack.

The revised display uses the occupied ROI, a finer trilinearly interpolated scalar field, two binomial filtering passes, a marching-tetrahedra isosurface, Taubin relaxation and shared vertex normals. The original binary mask, original measurement statistics, and source OBJ/STL mesh are unchanged. Users can switch the source surface and the display-only sampling ratio independently.

| Measured item | Result |
|---|---|
| Research-label union | 75,846 original voxels |
| Source mesh | 1,208 vertices / 2,412 triangles |
| Display preview | 65,374 vertices / 130,744 triangles |
| Source-mask SHA-256 | acfe6be4210024a9b070201f98154e61d7814eaa6548fe947d0a454f6f5e6f44 |
| Preview smoothing | Taubin, 10 iterations, lambda 0.5, mu −0.53 |
| Largest displacement before display scaling | 1.2636362686 source voxel units (Taubin relaxation after isosurface extraction) |
| Physical volume | null; no verified mm³ |
| Python tests | 195 passed |
| Renderer numerical/input/lifecycle checks | 7 passed, recording doubles; browser visual QA recorded separately |

### Matched source and spatial uncertainty

The provided reference-label array is an exact voxel match to the original `patient057_frame01_gt.nii` in the user's source dataset, after axis ordering Z/Y/X. The original image has the same grid and intensity correlation 0.998297 with the preprocessed application volume; the intensities are not an exact match.

The matched GT NIfTI header has pixdim X/Y/Z = 0.703125 / 0.703125 / 10. Its spatial unit code is 0 (unknown); qform is unset; sform code 2 has column norms 1/1/1, which conflict with pixdim. Therefore the ratio X/Y/Z = 1 / 1 / 14.22222222 is a labeled display option only. It is not physical geometry verification, patient orientation, a measurement calibration, or a change to exported coordinates. The source JSON binds this option to the current image and label hashes.

Original GT NIfTI SHA-256: 066da52171e94f28f4c42a124e67c0f888d383e773e843d39479e34b6f9cf67c.

ACDC reference: Bernard O., Lalande A., Zotti C. et al. Deep Learning Techniques for Automatic MRI Cardiac Multi-structures Segmentation and Diagnosis: Is the Problem Solved? IEEE TMI 37(11):2514–2525, 2018. https://doi.org/10.1109/TMI.2018.2837502
NIfTI field reference: https://nifti.nimh.nih.gov/nifti-1/documentation/nifti1fields

This partial 8-slice research-label reconstruction does not restore a complete heart, coronary anatomy, or a validated surgical model.

## Rebuilding and public integrity

`python scripts/build_public.py` retains the existing large source/data base archive and emits a small deterministic update archive containing differences. Both SHA-256 values are verified by the browser worker before Python starts. `--full` produces a new complete base archive. The public demo computes each result from source in the browser; no precomputed surface or generated heart illustration is substituted.

The interpolated preview adapts its sampling grid to a 180,000-triangle budget. A regression test covers the supplied thin class-2 label (25,573 voxels); viewer failures clear any earlier surface instead of retaining another result.
