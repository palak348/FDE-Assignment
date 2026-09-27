# Forward Deployed Engineering: Assignments

**Palak Agrawal**

## Assignments

| Assignment | Topic | Contents |
|---|---|---|
| [Assignment 2](assignment-2/) | Data Foundations (Classes 4–8) | A repeatable pipeline from raw NYC taxi data to a business KPI: did congestion pricing make Manhattan taxis faster? Includes the [2-page report](assignment-2/10504_PALAK_AGRAWAL.pdf). |

## In-class challenge notebooks

Completed FlashEats challenge notebooks from the in-class sessions, with their data. See [`class_notebooks/`](class_notebooks/) for details.

| Class | Topic | Notebook | Main finding |
|---|---|---|---|
| 5 | Data retrieval | [FlashEats_Class5_Student](class_notebooks/flasheats/FlashEats_Class5_Student.ipynb) | Late deliveries build up before pickup; the dispatch API needs retries, and all 1,600 records are proven retrieved |
| 6 | Data validation | [FlashEats_Class6_Student](class_notebooks/flasheats/FlashEats_Class6_Student.ipynb) | The "56% late" figure is reproducible but not publishable: definitions range from 23% to 57% and no KPI owner exists |
| 7 | Workflow modelling | [FlashEats_Class7_Challenge](class_notebooks/flasheats/FlashEats_Class7_Challenge.ipynb) | Order-centric model; 74% of frustrated late journeys received no intervention |

## Repository structure

```
├── assignment-2/        NYC congestion pricing KPI pipeline, report and documentation
└── class_notebooks/     completed FlashEats challenge notebooks (Classes 5–7) with their data
```

Each folder is self-contained, with its own README, code, data and instructions for running it.
