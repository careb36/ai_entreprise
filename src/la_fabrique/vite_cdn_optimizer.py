"""
Vite CDN Optimizer module for La Fabrique frontend build process.
Handles chunk analysis, Cache-Control header strategy generation,
and Lighthouse performance score delta verification.
"""

from typing import Dict, List, Any, Tuple


class ViteCDNOptimizer:
    """Manages Vite bundle optimization parameters and CDN cache header strategies."""

    IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
    MUTABLE_CACHE_CONTROL = "no-cache, no-store, must-revalidate"

    def __init__(self, baseline_score: float = 72.0):
        self.baseline_score = baseline_score

    def get_cache_control_header(self, filepath: str) -> str:
        """Determines the appropriate Cache-Control header based on file path and hashing status."""
        if "/assets/" in filepath or "assets/" in filepath:
            # Hashed static assets in assets/ folder get long-term immutable caching
            return self.IMMUTABLE_CACHE_CONTROL
        # Entry points (e.g. index.html) or root files require revalidation
        return self.MUTABLE_CACHE_CONTROL

    def evaluate_chunking_strategy(self, modules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Evaluates module splitting into manual chunks.
        Returns grouped vendor chunks and total estimated bundle metrics.
        """
        chunks: Dict[str, List[str]] = {
            "vendor-core": [],
            "vendor-icons": [],
            "vendor": [],
            "app-code": [],
        }

        for mod in modules:
            mod_id = mod.get("id", "")
            if "node_modules" in mod_id:
                if any(pkg in mod_id for pkg in ["react", "react-dom", "react-router-dom"]):
                    chunks["vendor-core"].append(mod_id)
                elif "lucide-react" in mod_id:
                    chunks["vendor-icons"].append(mod_id)
                else:
                    chunks["vendor"].append(mod_id)
            else:
                chunks["app-code"].append(mod_id)

        return {
            "chunks": chunks,
            "chunk_count": len([k for k, v in chunks.items() if len(v) > 0]),
            "is_optimized": len(chunks["vendor-core"]) > 0,
        }

    def calculate_lighthouse_score(self, metrics: Dict[str, float]) -> float:
        """
        Calculates simulated Lighthouse Performance score (0-100 scale)
        based on core Web Vitals (FCP, LCP, TTI, TBT, CLS).
        """
        fcp = metrics.get("fcp_ms", 2000.0)
        lcp = metrics.get("lcp_ms", 4000.0)
        tti = metrics.get("tti_ms", 3800.0)
        tbt = metrics.get("tbt_ms", 300.0)
        cls = metrics.get("cls", 0.1)

        # Weightings matching Lighthouse v10 performance metric weights
        fcp_score = max(0.0, min(100.0, 100 - (fcp - 900) * 0.03))
        lcp_score = max(0.0, min(100.0, 100 - (lcp - 1200) * 0.025))
        tti_score = max(0.0, min(100.0, 100 - (tti - 1500) * 0.02))
        tbt_score = max(0.0, min(100.0, 100 - (tbt - 100) * 0.15))
        cls_score = max(0.0, min(100.0, 100 - cls * 400))

        total = (
            fcp_score * 0.10
            + lcp_score * 0.25
            + tti_score * 0.10
            + tbt_score * 0.30
            + cls_score * 0.25
        )
        return round(total, 1)

    def verify_performance_gain(
        self, unoptimized_metrics: Dict[str, float], optimized_metrics: Dict[str, float]
    ) -> Tuple[bool, float, float, float]:
        """
        Verifies whether the Lighthouse performance score improved by at least 15 points.
        Returns (meets_ac, baseline_score, optimized_score, score_delta).
        """
        score_before = self.calculate_lighthouse_score(unoptimized_metrics)
        score_after = self.calculate_lighthouse_score(optimized_metrics)
        delta = round(score_after - score_before, 1)
        meets_ac = delta >= 15.0

        return meets_ac, score_before, score_after, delta
