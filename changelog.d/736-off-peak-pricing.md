Changed

* **A call is charged the rate in force when it was made (issue #736).** DeepSeek charges half outside its published peak hours (01:00–04:00 and 06:00–10:00 UTC on weekdays; every hour on weekends and Chinese public holidays). Cost reports used to price every call at the peak rate; a call is now priced by its recorded time with the same hours and holiday calendar as the run window's DeepSeek preset (`model_prices.at_time`), and a call without a time is still charged in full. Run, task, sprint and usage costs carry `off_peak_turns`.
