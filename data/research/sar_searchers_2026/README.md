# Anonymized GPS Tracks of Wilderness SAR Searchers

## Overview

This dataset contains anonymized GPS tracks of searchers participating in six wilderness search and rescue (SAR) team deployments. The data supports the manuscript:

**“Speed, slope, and synchrony: Empirical insights into SAR searcher behavior.”**

Each row represents a single GPS observation for an individual searcher.

---

## File Description

**SARsearchertracks.csv**

- 61 unique searcher tracks  
- 45,202 total observations  
- Coordinates in WGS84 (decimal degrees)  
- Elevation in meters  
- Time reported relative to the start of each track  

---

## Variables

**search_type**  
Deployment type: hasty, sweep, or paired.

**team_id**  
Anonymous identifier for search team.

**searcher_id**  
Anonymous identifier for individual within team.

**track_id**  
Unique identifier combining search type, team, and searcher.

**time_s**  
Relative time (HH:MM:SS).

**time_sec**  
Relative time in numeric seconds.

**latitude**  
Latitude (decimal degrees, WGS84).

**longitude**  
Longitude (decimal degrees, WGS84).

**elevation_m**  
Elevation (meters).

---

## Anonymization

All identifying metadata from the original GPS files has been removed, including:

- Device identifiers  
- Absolute timestamps  
- Incident-related metadata  

Time values are reported relative to track start to preserve anonymity.

---

## Reproducibility

This dataset enables reproduction of analyses described in the associated manuscript, including:

- Speed estimation  
- Slope-dependent movement modeling  
- Spearman rank correlation of teammate speeds  
- Cross-correlation metrics  
- Leader centrality analysis  
- Transfer entropy estimation  

---