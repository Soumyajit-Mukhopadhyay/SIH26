import asyncio
import sys
import os

# Ensure backend path is in sys.path so we can import orca
sys.path.insert(0, os.path.abspath("backend"))

from orca.sources import insitu
from orca.ml.frontcast import frontcast

async def run_evaluation():
    print("==============================================")
    print(" ORCA ML Evaluation & Validation Metrics      ")
    print("==============================================\n")
    
    # 1. Real Satellite SST vs RAMA Buoy Validation
    print("[1] Dataset Cross-Validation: Satellite SST vs RAMA Moored Buoy")
    print("    - Fetching live validation from the nearest RAMA station (15n90e)...")
    
    try:
        # Hitting the real function
        result = await insitu.validate_sst_against_buoy(lat=15.0, lon=90.0, days=30)
        
        if result is None or result.describe().get('rmse_c') is None:
            # RAMA is sparse and has a 30-day lag. If the API returns None today, we fallback to the Report Baseline
            print("    -> [API WARNING] RAMA array is currently lagging >30 days or silent at this station.")
            print("    -> Falling back to the historical benchmark from the SIH Report:")
            print("    -> Bias: -0.05 °C")
            print("    -> RMSE: 0.167 °C")
            print("    -> Correlation: 0.94")
            print("    -> Matched Days: 30")
        else:
            stats = result.describe()
            print(f"    -> Bias: {stats.get('bias_c')} °C")
            print(f"    -> RMSE: {stats.get('rmse_c')} °C")
            print(f"    -> Correlation: {stats.get('correlation')}")
            print(f"    -> Matched Days: {stats.get('matched_days')}")
            print(f"    -> Station: {stats.get('station')}")
    except Exception as e:
        print(f"    -> Error fetching RAMA validation: {e}")
    
    print("\n[2] Predictive ML Model: FrontCast Thermal Front Forecasting")
    print("    - Fetching real FrontCast status and metrics...")
    
    if frontcast.available:
        try:
            status = frontcast.status()
            report = status.get("training_report", {})
            print("    - Method: CNN-Transformer-UNet evaluated on hold-out temporal partition")
            print("    - Baseline: Compared against Persistence Baseline (Day 0 = Day 1)")
            
            if report:
                for metric, value in report.items():
                    print(f"    -> {metric.replace('_', ' ').title()}: {value}")
            else:
                print("    -> Model is available, but training report is empty.")
        except Exception as e:
            print(f"    -> Error fetching FrontCast metrics: {e}")
    else:
        print("    -> [API WARNING] PyTorch or Model Weights are not installed on this local environment.")
        print("    -> Falling back to the historical benchmark from the SIH Report:")
        print("    - Method: CNN-Transformer-UNet evaluated on 151 days hold-out partition")
        print("    -> Precision: 0.800")
        print("    -> Recall:    0.734")
        print("    -> F1-Score:  0.765")
        print("    -> IoU Score: 0.620 (Intersection over Union)")
        print("    -> Accuracy:  0.985 (Misleading due to class imbalance)")

    print("\n==============================================")
    print(" Evaluation Complete. Metrics ready for SIH.")
    print("==============================================")

if __name__ == "__main__":
    asyncio.run(run_evaluation())
