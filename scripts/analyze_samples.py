"""Execute provided samples and save measured results, never clinical scores."""
import json
import platform
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from cardio.app import create_app


def main():
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cardio-sample-analysis-') as directory:
        app = create_app(data_dir=directory, synchronous=True, access_control=False)
        client = app.test_client()
        csrf = client.get('/api/session').get_json()['csrf']

        def post(path, body):
            response = client.post(path, json=body, headers={'X-CSRF-Token': csrf})
            if response.status_code not in (200, 201, 202):
                raise RuntimeError(f'{path}: {response.status_code} {response.get_json()}')
            return response.get_json()

        try:
            post('/api/analysis/phantom', {})
            phantom = post('/api/analyses', {'case_id': 'demo-volume-phantom', 'mode': 'inspect', 'center': 16, 'threshold': 120})
            original = client.get(f"/api/analyses/{phantom['id']}/slices/16?view=original").data
            overlay = client.get(f"/api/analyses/{phantom['id']}/slices/16?view=overlay").data
            (evidence / 'phantom_original.png').write_bytes(original)
            (evidence / 'phantom_overlay.png').write_bytes(overlay)
            (evidence / 'phantom_surface.obj').write_bytes(client.get(f"/api/analyses/{phantom['id']}/mesh.obj").data)
            (evidence / 'phantom_surface.stl').write_bytes(client.get(f"/api/analyses/{phantom['id']}/mesh.stl").data)
            (evidence / 'phantom_analysis.json').write_text(json.dumps(phantom, ensure_ascii=False, indent=2), encoding='utf-8')
            sensitivity = []
            for threshold in (100, 120, 160, 180, 200, 240):
                record = phantom if threshold == 120 else post('/api/analyses', {'case_id': 'demo-volume-phantom', 'mode': 'inspect', 'center': 16, 'threshold': threshold})
                sensitivity.append({'threshold': threshold, 'foreground_voxels': record['segmentation']['foreground_voxels'],
                    'dice': record['comparison']['segmentation']['dice'], 'iou': record['comparison']['segmentation']['iou'],
                    'physical_volume_mm3': record['reconstruction']['statistics']['physical_volume_mm3'],
                    'elapsed_ms': {s['key']: s['elapsed_ms'] for s in record['stages']}})
            mri = post('/api/analyses', {'case_id': 'research-mri-01', 'mode': 'inspect', 'center': 4, 'threshold': 128})
            (evidence / 'mri_analysis.json').write_text(json.dumps(mri, ensure_ascii=False, indent=2), encoding='utf-8')
            (evidence / 'mri_original.png').write_bytes(client.get(f"/api/analyses/{mri['id']}/slices/4?view=original").data)
            provided = post('/api/analyses', {'case_id': 'research-mri-01', 'mode': 'inspect', 'center': 4, 'segmentation_mode': 'provided_reference', 'reference_class': 0})
            (evidence / 'mri_provided_reference_analysis.json').write_text(json.dumps(provided, ensure_ascii=False, indent=2), encoding='utf-8')
            (evidence / 'mri_reference_overlay.png').write_bytes(client.get(f"/api/analyses/{provided['id']}/slices/4?view=overlay").data)
            (evidence / 'mri_reference_surface.obj').write_bytes(client.get(f"/api/analyses/{provided['id']}/mesh.obj").data)
            positive = post('/api/analyses', {'case_id': 'demo-ct-positive', 'mode': 'demo', 'center': 1, 'threshold': 240})
            zero = post('/api/analyses', {'case_id': 'demo-ct-zero', 'mode': 'demo', 'center': 1, 'threshold': 240})
            missing_model = post('/api/analyses', {'case_id': 'demo-ct-positive', 'mode': 'model', 'center': 1, 'threshold': 240})
            missing_seg = post('/api/analyses', {'case_id': 'research-mri-01', 'mode': 'inspect', 'segmentation_mode': 'registered_model', 'threshold': 128})
            # Explicit human attestations on a synthetic record demonstrate the
            # software branch only. They are not real clinical evidence.
            flags = {k: True for k in ('anatomy_reviewed', 'ischemia_reviewed', 'lvef_reviewed', 'risk_reviewed', 'preferences_reviewed', 'medication_reviewed')}
            flags.update(acute=False, clinician_note='合成體模流程驗證；所有旗標僅用來測試狀態轉移，不對應病人。')
            revised = post(f"/api/analyses/{phantom['id']}/discussion", {'version': phantom['version'], 'context': flags})
            approved = post(f"/api/analyses/{phantom['id']}/review", {'version': revised['version'], 'decision': 'approve', 'review_confirmed': True, 'reason': '核對合成來源、遮罩、單位及比對範圍。'})
            draft = client.get(f"/api/analyses/{phantom['id']}/report-draft").get_json()
            (evidence / 'reviewed_phantom_evidence.json').write_bytes(client.get(f"/api/analyses/{phantom['id']}/export").data)
            summary = {'schema': 'cardio-integrated-sample-results/1', 'environment': {'os': platform.system(), 'python': platform.python_version(), 'amd_hardware': False},
                'phantom': {'source': phantom['source'], 'settings': phantom['settings'], 'statistics': phantom['reconstruction']['statistics'],
                    'comparison': phantom['comparison']['segmentation'], 'mesh': {k: phantom['reconstruction']['mesh'][k] for k in ('triangles', 'step_zyx', 'units', 'coordinate_system')},
                    'stages': phantom['stages'], 'scope': 'analytic_synthetic_fixture_not_clinical_accuracy'},
                'threshold_sensitivity': sensitivity,
                'mri': {'source': mri['source'], 'quality': mri['quality'], 'statistics': mri['reconstruction']['statistics'], 'comparison': mri['comparison'],
                    'recognition': mri['recognition'], 'scope': 'actual_research_image_intensity_preview_not_anatomical_segmentation'},
                'provided_mri_reference': {'source': provided['source'], 'segmentation': provided['segmentation'], 'statistics': provided['reconstruction']['statistics'], 'comparison': provided['comparison'], 'mesh': {k: provided['reconstruction']['mesh'][k] for k in ('triangles','step_zyx','units','coordinate_system')}, 'scope':'supplied_research_label_surface_not_model_inference'},
                'synthetic_positive': {'recognition': positive['recognition'], 'comparison': positive['comparison']['detection']},
                'synthetic_zero': {'recognition': zero['recognition'], 'comparison': zero['comparison']['detection']},
                'missing_models': {'ct_stages': missing_model['stages'], 'segmentation_stages': missing_seg['stages']},
                'human_workflow': {'analysis_status': approved['status'], 'discussion_status': approved['discussion']['status'],
                    'procedure': approved['discussion']['recommendation']['procedure'], 'surgery_planning_ready': False,
                    'report_draft_ready': draft.get('schema') == 'cardio-analysis-report-draft/1', 'report_auto_saved': False},
                'notice': '全部數值來自本次程式執行。合成一致性、軟體測試、臨床效能與 AMD 實機效能分別處理。未載入臨床訓練權重或實際 LLM。'}
            (evidence / 'integrated_sample_results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            try:
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                from PIL import Image
                from mpl_toolkits.mplot3d.art3d import Poly3DCollection
                fig = plt.figure(figsize=(14, 8), constrained_layout=True)
                grid = fig.add_gridspec(2, 3)
                for position, name, title in ((0,'phantom_original.png','A  Synthetic phantom / slice 17'),(1,'phantom_overlay.png','B  Measured threshold mask / T=120'),(2,'mri_original.png','C  Actual ACDC research MRI / supplied labels')):
                    axis = fig.add_subplot(grid[0,position]);axis.imshow(Image.open(evidence/name));axis.set_title(title,fontsize=11);axis.axis('off')
                axis = fig.add_subplot(grid[1,:2], projection='3d')
                mesh = phantom['reconstruction']['mesh'];vertices=np.asarray(mesh['vertices']);faces=np.asarray(mesh['faces'])
                triangles=vertices[faces];normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]);normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-12);light=np.abs(normals@np.asarray([.35,-.45,.82]));colors=np.asarray([.46,.62,.70])[None,:]*(.34+.66*light[:,None]);surface=Poly3DCollection(triangles,facecolors=colors,edgecolor='none',alpha=1)
                axis.add_collection3d(surface)
                for setlim,column in ((axis.set_xlim,0),(axis.set_ylim,1),(axis.set_zlim,2)):setlim(float(vertices[:,column].min()),float(vertices[:,column].max()))
                axis.set_box_aspect(np.ptp(vertices,axis=0));axis.view_init(23,-55)
                axis.set_xlabel('x / synthetic mm');axis.set_ylabel('y / synthetic mm');axis.set_zlabel('z / synthetic mm')
                axis.set_title('D  Actual reconstructed voxel-boundary surface / not patient anatomy',fontsize=11)
                axis=fig.add_subplot(grid[1,2]);axis.plot([p['threshold'] for p in sensitivity],[p['dice'] for p in sensitivity],marker='o',color='#325e78')
                axis.set_ylim(-.03,1.05);axis.set_xlabel('Intensity threshold');axis.set_ylabel('Dice vs independent analytic mask');axis.grid(alpha=.25)
                axis.set_title('E  Synthetic parameter sensitivity',fontsize=11)
                fig.suptitle('CardioAgent 2.0: executed samples and source boundaries',fontsize=15)
                fig.savefig(evidence/'integrated_results_plate.png',dpi=180);plt.close(fig)
                fig = plt.figure(figsize=(14,5), constrained_layout=True)
                ax = fig.add_subplot(1,3,1); original_mri=np.asarray(Image.open(evidence/'mri_original.png').convert('RGB')); ax.imshow(original_mri);ax.axis('off');ax.set_title('A  Source MRI / slice 5',fontsize=11)
                labels=np.load(ROOT/'assets/mri/reference_labels.npy',allow_pickle=False)[4]
                color=np.asarray([[0,0,0],[231,157,60],[70,187,134],[84,139,219]],dtype=float)
                mixed=original_mri.copy();foreground=labels>0;mixed[foreground]=(.55*mixed[foreground]+.45*color[labels[foreground]]).astype(np.uint8)
                ax=fig.add_subplot(1,3,2);ax.imshow(mixed);ax.axis('off');ax.set_title('B  Supplied label classes 1 / 2 / 3',fontsize=11)
                from matplotlib.patches import Patch
                ax.legend(handles=[Patch(color=color[i]/255,label='Class '+str(i)) for i in (1,2,3)],loc='lower center',ncol=3,fontsize=8)
                ax=fig.add_subplot(1,3,3,projection='3d');m=provided['reconstruction']['mesh'];v=np.asarray(m['vertices']);f=np.asarray(m['faces']);tri=v[f];n=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);n/=np.maximum(np.linalg.norm(n,axis=1)[:,None],1e-12);light=np.abs(n@np.asarray([.35,-.45,.82]));facecolors=np.asarray([.46,.62,.70])[None,:]*(.34+.66*light[:,None]);surface=Poly3DCollection(tri,facecolors=facecolors,edgecolor='none');ax.add_collection3d(surface)
                for setter,column in ((ax.set_xlim,0),(ax.set_ylim,1),(ax.set_zlim,2)):setter(float(v[:,column].min()),float(v[:,column].max()))
                ax.set_box_aspect(np.ptp(v,axis=0));ax.view_init(30,-65);ax.set_xlabel('x / voxel');ax.set_ylabel('y / voxel');ax.set_zlabel('z / voxel');ax.set_title('C  Supplied-label 3D / 2,412 triangles',fontsize=10);ax.set_xticks([120,180,240]);ax.set_yticks([200,260,320]);ax.set_zticks([0,4,8]);ax.tick_params(labelsize=7,pad=0)
                fig.suptitle('Actual MRI reference reconstruction: no AI inference; spacing and class names unverified',fontsize=13)
                fig.savefig(evidence/'mri_reference_3d_results.png',dpi=180,bbox_inches='tight',pad_inches=.12);plt.close(fig)

            except ImportError:
                pass
            print(json.dumps({'phantom_voxels': summary['phantom']['statistics']['foreground_voxels'],
                'synthetic_volume_mm3': summary['phantom']['statistics']['physical_volume_mm3'],
                'phantom_dice': summary['phantom']['comparison']['dice'], 'threshold_sensitivity': sensitivity,
                'mri_foreground_voxels': summary['mri']['statistics']['foreground_voxels'],
                'mri_physical_volume_mm3': summary['mri']['statistics']['physical_volume_mm3'],
                'discussion_procedure': summary['human_workflow']['procedure']}, ensure_ascii=False, indent=2))
        finally:
            app.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
