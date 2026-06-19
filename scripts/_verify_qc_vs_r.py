# SPDX-License-Identifier: AGPL-3.0-only
# -*- coding: utf-8 -*-
"""Verify the data-quality page backend against the R script
01_Camera_trap_location_analysis.qmd.

Instead of running R we replicate its logic in Python using three-valued NA
(pandas BooleanDtype/Int64). Logic matches the R script exactly:

  qc_no_GPS_coordinates = is.na(lat) | is.na(lon)
  qc_data_not_usable    = qc_data_not_usable | qc_no_GPS_coordinates |
                          qc_feeding_location | qc_hardware_issue |
                          (qc_installation_incorrect & qc_no_species_captured) |
                          (qc_placement_incorrect    & qc_no_species_captured) |
                          (qc_poor_placement         & qc_no_species_captured)
  qc_summary            = qc_data_not_usable | qc_no_data_uploaded_by_PA |
                          qc_sd_issue | qc_stolen | qc_non_functional
  qc_min_days_not_reached = (season=='Winter' & n_days<100) | (season=='Summer' & n_days<60)

For each QC parameter we compute Issue / Normal / Missing counts — as on the
data-quality chart — then compare «R-reference (from Excel)» with
«DB-derived (as our data_quality route computes)» and report discrepancies.
"""
import warnings
warnings.filterwarnings('ignore')
import pandas as pd
import numpy as np

from app import create_app
from app.camera_traps.database import get_ct_session, close_ct_session
from app.camera_traps.models import Deployment, Location, location_institutions
from app.camera_traps.deployment_import import normalize_header, ALIAS_MAP, INVERT_SOURCES, IGNORED_COLS
from app.camera_traps.routes import QC_FILTER_ORDER, _b, _kor, _kand
from sqlalchemy import select


XLSX = 'CT_LocationARD_Dataset.xlsx'
SHEETS = ['SMM_2023','Data 2023-2024','WLCM_2023-24','SMM_2024',
          'WLCM_2024-2025','SMM_2025','WLCM_2025-26']

# Canonical boolean QC fields (DB column names, lowercase)
QC_STORED = [
    'qc_non_functional','qc_stolen','qc_hardware_issue','qc_firmware_issue',
    'qc_settings_issue','qc_battery_issue','qc_sd_issue','qc_no_data_uploaded_by_pa',
    'qc_uploaded_data_is_not_raw','qc_no_species_captured','qc_placement_incorrect',
    'qc_poor_placement','qc_feeding_location','qc_installation_incorrect',
    'qc_lapse_photos_missed','qc_installation_photos_missed','qc_deinstallation_photos_missed',
    'qc_distance_reference_photos_missed','qc_datetime_photos_missed',
    'qc_local_datetime_not_set','qc_data_not_usable',
]
# Derived (computed; not read directly from Excel)
QC_DERIVED = ['qc_no_gps_coordinates','qc_summary','qc_min_days_not_reached']
QC_ALL = QC_STORED + QC_DERIVED


def coerce_excel_bool(v):
    """Same coercion logic as the deployment importer. NA variants → pd.NA; true/false → True/False."""
    if pd.isna(v):
        return pd.NA
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if v in (0, 1):
            return bool(v)
        return pd.NA
    s = str(v).strip().lower().replace('\xa0', '')
    if s in {'1','1.0','true','yes','y','x','так','+'}: return True
    if s in {'','0','0.0','false','no','n','ні','-','none'}: return pd.NA if s=='' else False
    return pd.NA


def build_r_reference():
    """Read the same Excel sheets and apply R logic exactly (3-valued NA semantics)."""
    frames = []
    for sheet in SHEETS:
        df = pd.read_excel(XLSX, sheet_name=sheet)
        # normalize column names via ALIAS_MAP/INVERT_SOURCES (same as in the importer)
        canon = {}  # canonical attr -> series
        for col in df.columns:
            norm = normalize_header(col)
            if norm in IGNORED_COLS: continue
            invert = norm in INVERT_SOURCES
            attr = INVERT_SOURCES[norm] if invert else ALIAS_MAP.get(norm)
            if not attr: continue
            if attr in ('__lat','__lon'): continue
            ser = df[col]
            if attr in QC_STORED + ['qc_used_brf']:
                ser = ser.map(coerce_excel_bool)
                if invert:
                    ser = ser.map(lambda x: pd.NA if pd.isna(x) else (not x))
                ser = ser.astype('boolean')
            elif attr == 'name':
                ser = ser.astype('string')
            else:
                pass
            # direct mapping takes priority over inversion — don't overwrite what's already set
            if attr not in canon:
                canon[attr] = ser
        # lat/lon as numeric
        for c in ('latitude','longitude'):
            if c in df.columns:
                canon[c] = pd.to_numeric(df[c], errors='coerce')
        # dates
        for c in ('start_date','end_date'):
            if c in df.columns:
                canon[c] = pd.to_datetime(df[c], errors='coerce')
        # study_season
        if 'study_season' in df.columns:
            canon['study_season'] = df['study_season'].astype('string')
        f = pd.DataFrame(canon)
        frames.append(f)
    df = pd.concat(frames, ignore_index=True)
    # drop rows without deployment_id (R filters by study_area_id; we filter by name presence)
    df = df[df['name'].notna()].reset_index(drop=True)

    n_days = (df['end_date'] - df['start_date']).dt.days
    df['n_days_working'] = n_days
    df['qc_no_gps_coordinates'] = (df['latitude'].isna() | df['longitude'].isna())

    # three-valued OR via pandas BooleanArray
    def b(col): return df[col].astype('boolean') if col in df.columns else pd.Series([pd.NA]*len(df), dtype='boolean')

    incorrect_install_no_species = b('qc_installation_incorrect') & b('qc_no_species_captured')
    incorrect_placement_no_species = b('qc_placement_incorrect') & b('qc_no_species_captured')
    poor_placement_no_species = b('qc_poor_placement') & b('qc_no_species_captured')
    derived_dnu = (b('qc_data_not_usable')
                   | df['qc_no_gps_coordinates'].astype('boolean')
                   | b('qc_feeding_location')
                   | b('qc_hardware_issue')
                   | incorrect_install_no_species
                   | incorrect_placement_no_species
                   | poor_placement_no_species)
    df['qc_data_not_usable'] = derived_dnu

    df['qc_summary'] = (derived_dnu | b('qc_no_data_uploaded_by_pa') | b('qc_sd_issue')
                        | b('qc_stolen') | b('qc_non_functional'))

    winter = (df['study_season'] == 'Winter')
    summer = (df['study_season'] == 'Summer')
    # comparisons with NA n_days produce NA
    days_lt100 = (n_days < 100).astype('boolean'); days_lt100[n_days.isna()] = pd.NA
    days_lt60  = (n_days < 60).astype('boolean');  days_lt60[n_days.isna()] = pd.NA
    season_winter = winter.astype('boolean'); season_winter[df['study_season'].isna()] = pd.NA
    season_summer = summer.astype('boolean'); season_summer[df['study_season'].isna()] = pd.NA
    df['qc_min_days_not_reached'] = ((season_winter & days_lt100) | (season_summer & days_lt60))

    return df


def status_counts(series):
    """Tally Issue (True) / Normal (False) / Missing (NA) — matching the data_quality chart."""
    s = series
    if not str(s.dtype).startswith('boolean'):
        s = s.astype('boolean')
    issue = int((s == True).sum())
    normal = int((s == False).sum())
    missing = int(s.isna().sum())
    return issue, normal, missing


def db_records_counts(records):
    """Same tallies but over records returned by our backend data_quality route."""
    out = {}
    for f in QC_ALL:
        i = n = m = 0
        for r in records:
            v = r.get(f)
            if v is True: i += 1
            elif v is False: n += 1
            else: m += 1
        out[f] = (i, n, m)
    return out


def build_db_records():
    """Replicate the record set produced by the /data-quality route for admin users."""
    app = create_app()
    with app.app_context():
        s = get_ct_session()
        try:
            deps = s.query(Deployment).all()
            loc_ids = [d.location_id for d in deps if d.location_id is not None]
            locs = {l.id: l for l in s.query(Location).filter(Location.id.in_(loc_ids)).all()} if loc_ids else {}
            records = []
            for d in deps:
                loc = locs.get(d.location_id) if d.location_id else None
                lat = float(loc.latitude) if loc and loc.latitude is not None else None
                lon = float(loc.longitude) if loc and loc.longitude is not None else None
                n_days = d.n_days_calc
                if n_days is None and d.start_date and d.end_date:
                    n_days = (d.end_date - d.start_date).days
                qc_no_gps = (lat is None or lon is None)
                data_not_usable = _kor(
                    d.qc_data_not_usable, qc_no_gps, d.qc_feeding_location, d.qc_hardware_issue,
                    _kand(d.qc_installation_incorrect, d.qc_no_species_captured),
                    _kand(d.qc_placement_incorrect, d.qc_no_species_captured),
                    _kand(d.qc_poor_placement, d.qc_no_species_captured),
                )
                summary = _kor(data_not_usable, d.qc_no_data_uploaded_by_pa,
                               d.qc_sd_issue, d.qc_stolen, d.qc_non_functional)
                mindays = None
                if n_days is not None and d.study_season:
                    if d.study_season == 'Winter': mindays = n_days < 100
                    elif d.study_season == 'Summer': mindays = n_days < 60
                rec = {'qc_no_gps_coordinates': qc_no_gps,
                       'qc_data_not_usable': data_not_usable,
                       'qc_summary': summary,
                       'qc_min_days_not_reached': mindays}
                for f in QC_STORED:
                    if f not in rec: rec[f] = getattr(d, f)
                records.append(rec)
            return records
        finally:
            close_ct_session()


def main():
    print('=== R-REFERENCE (Python re-implementation, 3-valued logic) ===')
    rdf = build_r_reference()
    print(f'rows (deployments) in reference: {len(rdf)}')
    r_counts = {f: status_counts(rdf[f]) for f in QC_ALL if f in rdf.columns}

    print('\n=== DB-DERIVED (as our /data-quality endpoint computes) ===')
    db_records = build_db_records()
    print(f'records from DB: {len(db_records)}')
    d_counts = db_records_counts(db_records)

    print('\n=== COMPARISON (Issue / Normal / Missing) ===')
    print(f'{"qc field":40} {"R-Issue":>7} {"DB-Issue":>8}  {"R-Norm":>7} {"DB-Norm":>7}  {"R-Miss":>7} {"DB-Miss":>7}  match')
    print('-' * 110)
    total_mismatch = 0
    for f in QC_ALL:
        if f not in r_counts: continue
        ri,rn,rm = r_counts[f]
        di,dn,dm = d_counts[f]
        match = (ri,rn,rm) == (di,dn,dm)
        marker = 'OK' if match else 'MISMATCH'
        if not match: total_mismatch += 1
        print(f'{f:40} {ri:>7} {di:>8}  {rn:>7} {dn:>7}  {rm:>7} {dm:>7}  {marker}')
    print('-' * 110)
    print(f'Total mismatched fields: {total_mismatch} / {len(QC_ALL)}')


if __name__ == '__main__':
    main()
