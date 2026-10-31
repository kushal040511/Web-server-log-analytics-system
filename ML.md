# Machine learning stage: web-attack detection

**Task:** supervised binary classification of HTTP requests. **2 classes:** 0 = normal, 1 = attack
(SQL injection, XSS, command injection, path traversal).
**Main model:** Factorization Machine (`FMClassifier`, Spark MLlib), compared with 7 Spark MLlib models.

## Run it

```bash
make ml          # = ml-data + ml-ingest + ml-prep + ml-train   (about 20-30 min on the laptop cluster)
```

| Target | What it does |
|---|---|
| `make ml-data` | `scripts/get_attack_data.sh` downloads HttpParams + Fwaf into `./data/attacks` (~29 MB) |
| `make ml-ingest` | copies them into HDFS: `/data/attacks` |
| `make ml-prep` | `jobs/ml_preprocess.py`: clean, bias fix, de-duplicate, 80/20 split → `/data/attacks/prepared` |
| `make ml-train` | `jobs/ml_train.py`: 8 models, confusion matrices, scores the NASA logs → `output/ml/ml_report.html` |

## Data and preprocessing (`jobs/ml_preprocess.py`)

| Step | Result |
|---|---|
| Load + label | 1,373,724 labelled requests (HttpParams 31,067 + Fwaf 1,342,657) |
| Clean | URL-decode, lower-case, trim; 67 empty rows removed |
| **Bias fix** | Before: 42.2% of attacks but only 2.5% of normal requests contained `?`, so a model could learn "has `?` = attack". Only the query part of a URL is kept. |
| De-duplicate | 66,987 duplicates removed; 501 requests with both labels removed |
| Final | **1,306,017** requests: 1,267,605 normal (97.06%), 38,412 attack (2.94%) |
| Split | Train **153,847** (all attacks + 4 normal per attack); Test **261,076** (real mix, never seen in training) |

**Features:** character 1-, 2- and 3-grams hashed into 3 × 4,096 buckets + length, special-character,
digit and keyword counts = **12,292 features**, scaled to 0–1. (The MLP uses a 388-feature version:
Spark's MLP needs dense input.)

## Results (`jobs/ml_train.py`, test set of 261,076 requests)

| Model | Accuracy | Precision | Recall | **F1** |
|---|---|---|---|---|
| Linear SVM | 0.9981 | 0.9494 | 0.9885 | **0.9686** |
| **Factorization Machine (ours)** | 0.9980 | 0.9498 | 0.9846 | **0.9669** |
| Logistic Regression | 0.9979 | 0.9453 | 0.9854 | **0.9649** |
| Decision Tree | 0.9966 | 0.9292 | 0.9586 | 0.9437 |
| Gradient Boosted Trees | 0.9926 | 0.8185 | 0.9609 | 0.8840 |
| Naive Bayes | 0.9920 | 0.9146 | 0.8033 | 0.8553 |
| Multilayer Perceptron | 0.9901 | 0.7738 | 0.9379 | 0.8480 |
| Random Forest | 0.9906 | 0.9571 | 0.7109 | 0.8158 |

**FM confusion matrix:** TN 253,012 · FP 399 · FN 118 · TP 7,547.
All 8 matrices: `docs/ml_results/confusion_matrices_all.png`.

FM is within 0.002 F1 of the best model (Linear SVM) and ahead of Logistic Regression and
all tree, probabilistic and neural models.

## On the NASA logs (case study, no labels)

The FM model scored all 31,026 distinct NASA URLs and flagged **120** (0.39%, 1,754 requests).
Before the bias fix it flagged 1,083. Checked by hand, most of the 120 are **not attacks**:
searches containing words like `select` or `transcript` ("trans**script**"), pages under
`/msfc/description/` ("de**script**ion"), and quotes in search terms. The rest are malformed requests
(binary garbage, `/.../liftoff.html`, pasted HTML such as `/elv/new01.gif><li><a href=`).
No confirmed attack was found. This is a **domain-shift limitation**: the training data is from modern
attack datasets, the NASA logs are 1995 traffic without labels.
