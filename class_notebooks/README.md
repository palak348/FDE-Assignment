# In-class challenge notebooks

Completed FlashEats challenge notebooks from the in-class sessions (required for every track). Each one keeps the original challenge prompts and adds working code, outputs and a short written answer after every challenge.

| Class | Topic | Notebook | Main finding |
|---|---|---|---|
| 5 | Retrieving data (SQL, CSV, JSON, API) | [`FlashEats_Class5_Student.ipynb`](flasheats/FlashEats_Class5_Student.ipynb) | 56.4% late by "any delay", 23.3% by >10 min. The delay builds up **before pickup** (picked up 14 min behind the dispatch plan vs 2 min for on-time orders). The API needs retries on pages 3 and 5, and all 1,600 records are proven retrieved. |
| 6 | Validation | [`FlashEats_Class6_Student.ipynb`](flasheats/FlashEats_Class6_Student.ipynb) | "56% late" is reproducible but **not publishable**: 4 definitions give 23% to 57% and no KPI owner exists. Gate: 4 WARN, 2 FAIL. |
| 7 | Modelling the workflow | [`FlashEats_Class7_Challenge.ipynb`](flasheats/FlashEats_Class7_Challenge.ipynb) | Order-centric model. Interventions show no difference in late rate as used today; 74% of frustrated late journeys got no intervention; the interventions log and dispatch disagree on reassignments. |

`flasheats/` also holds a copy of the classroom data (`database/`, `data/`, `api/`), so the notebooks run as they are:

```bash
pip install pandas matplotlib requests flask jupyter
cd class_notebooks/flasheats
jupyter notebook
```

The Class 5 notebook starts the mock dispatch API itself and saves every raw API page to `student_output/raw_dispatch/`.
