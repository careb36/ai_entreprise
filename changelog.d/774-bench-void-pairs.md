Fixed

* **Repeated bench runs no longer void a pair over the ScrumMaster.** `compare.py` with repeated cards compared every role either run served, so a run that blocked a task and called the ScrumMaster voided its pair, and enough voided pairs left every metric unmeasured. It now compares the models over the specifications both cards ran and the roles both sides served, as the paired test already did; a pair with no specification in common stays void. The paired test now uses the same pairing.
