# Project Mirror: Migration Report
**Source**: `.gemini/antigravity/experiments/Project_Mirror`
**Destination**: `/Users/hadi/Development/Mirror`
**Date**: 2025-12-29

## ✅ Migration Successful
All core components of Project Mirror (Probe, Cloud Sync, 16D Pulse, and Dashboard) have been moved to this directory.

## 🚀 Next Steps: VPS Deployment
To move this to your VPS, follow these steps:

1. **Copy to VPS**:
   ```bash
   scp -r /Users/hadi/Development/Mirror user@vps-ip:/path/to/destination
   ```

2. **Setup on VPS**:
   ```bash
   cd Mirror
   python3 -m venv venv
   source venv/bin/activate
   pip install supabase openai pypdf python-dotenv
   ```

3. **Environment**:
   - Ensure you copy your `.env` file to the VPS (it was excluded from the repo for security, but I've copied it here safely if you ran the sync locally). 
   - *Note: Check if `.env` exists in this folder. If not, create one with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `OPENAI_API_KEY`.*

4. **Run the Pulse**:
   ```bash
   python3 mirror_pulse.py --log "Deploying to VPS infrastructure." --desc "System Migration"
   ```

## 📂 Directory Structure
- `mirror_probe_pdf.py`: Zenodo Ingestion Tool
- `mirror_pulse.py`: 16D Witness Backend
- `dashboard-app/`: React Visualization Frontend
- `mirror_sync_remote.py`: Cloud Persistence Script
- `schema.sql`: Database Definition

## 🏛️ Archive (Legacy Research)
Files from previous experiments have been preserved in `Archive/`:
- **Project Chimera**: All 13 versions of the Continual Learning experiment.
- **Artifacts**: Manifestos, Post-mortems, and FRC Draft Papers.
- **Scratchpad**: Early Alpha Drift prototypes.

---
**Status**: Ready for Deployment.
