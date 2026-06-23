import pandas as pd
import numpy as np


def calculate_subsidy_claim():

    data_file =  "data/master-kibor/master_kibore.xlsx"

    df = pd.read_excel(data_file)
    
    df["End User Rate"] = np.select(
        [
            df["Tier Code"] == 1,
            df["Tier Code"] == 2,
            df["Tier Code"] == 3,
        ],
        [
            3,
            4,
            5,
        ]
    )

    
    df["Subsidy Claim M1"] =  (df["OAS M1"].abs() * ((df["M1 Kibor"] + 4 - df["End User Rate"]) / 100)) / 12 
    df["Subsidy Claim M2"] = (df["OAS M2"].abs() * ((df["M2 Kibor"] + 4 - df["End User Rate"]) / 100)) / 12
    df["Subsidy Claim M3"] = (df["OAS M3"].abs() * ((df["M3 Kibor"] + 4 - df["End User Rate"]) / 100)) / 12
    df["Total Subsidy Claim"] = df["Subsidy Claim M1"] + df["Subsidy Claim M2"] + df["Subsidy Claim M3"]
    
    total_subsidy = round(df["Total Subsidy Claim"].sum())

    # Total row
    total_row = {col: None for col in df.columns}
    total_row[df.columns[0]] = "Grand Total"
    total_row["Total Subsidy Claim"] = total_subsidy

    df.loc[len(df)] = total_row
    df.to_excel(data_file, index=False)
    return df



