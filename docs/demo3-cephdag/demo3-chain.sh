#!/bin/sh
# Run the four demo3 campaigns one after another on cephdag8; stop at the first failure.
set -eu
cd ~/experiments/cephdag-demo3
git log --oneline -1
python3 experiments/campaign.py run experiments/campaigns/demo3-sweep.json
echo "demo3-sweep complete at $(TZ=America/New_York date '+%F %T %Z')"
python3 experiments/campaign.py run experiments/campaigns/demo3-skew.json
echo "demo3-skew complete at $(TZ=America/New_York date '+%F %T %Z')"
python3 experiments/campaign.py run experiments/campaigns/demo3-droid.json
echo "demo3-droid complete at $(TZ=America/New_York date '+%F %T %Z')"
python3 experiments/campaign.py run experiments/campaigns/demo3-scale.json
echo "demo3-scale complete at $(TZ=America/New_York date '+%F %T %Z')"
