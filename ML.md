# Machine learning stage: web-attack detection

**Task:** supervised binary classification of HTTP requests. **2 classes:** 0 = normal, 1 = attack
(SQL injection, XSS, command injection, path traversal).
**Main model:** Factorization Machine (`FMClassifier`, Spark MLlib), compared with 7 Spark MLlib models.

## Run it

```bash
make ml          # every target below, in order (about 2 hours on the laptop cluster)
```

| Target | What it does |
|---|---|
| `make ml-data` | `scripts/get_attack_data.sh` downloads HttpParams + Fwaf into `./data/attacks` (~29 MB) |
| `make ml-ingest` | copies them into HDFS: `/data/attacks` |
| `make ml-prep` | `jobs/ml_preprocess.py`: clean, bias fix, de-duplicate, 80/20 split → `/data/attacks/prepared` |
| `make ml-tune` | `jobs/ml_tune.py`: checks the model settings on a validation split of the training set (test set untouched) |
| `make ml-train` | `jobs/ml_train.py`: 8 models, confusion matrices, scores the NASA logs → `output/ml/ml_report.html` |
| `make ml-cross` | `jobs/ml_crossdataset.py`: train on one dataset, test on the other (top 3 models, 5 seeds) |
| `make ml-robust` | `jobs/ml_robustness.py`: top 3 models on 5 random splits; mean ± std, ROC-AUC, PR-AUC, McNemar's test |
| `make ml-audit` | `scripts/audit_nasa_flags.py`: sorts the NASA URLs the model flags into hand-checked categories |

Then `make bi`: the results appear in the **5 · Attack detection (ML)** dashboard in Apache Superset (http://localhost:8088).

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

On this split (seed 42) the FM is within 0.002 F1 of the best model (Linear SVM) and ahead of all tree,
probabilistic and neural models. One split can be lucky, so the top 3 are repeated on 5 more splits below.

## Model settings (`jobs/ml_tune.py`, validation set only)

The balanced training set is split again 80/20; settings are trained on the first part and scored on the
second. The test set is never read. Each linear model gets the same small grid.

| Model | Grid | Best (validation F1) |
|---|---|---|
| Factorization Machine | factorSize {4, 8, 16} × stepSize {0.05, 0.2}, maxIter 100 | factorSize 8, stepSize 0.2 (0.9882) |
| Linear SVM | regParam {1e-5, 1e-4, 1e-3} | 1e-4 (0.9923) |
| Logistic Regression | regParam {1e-5, 1e-4, 1e-3} | 1e-4 (0.9890) |

The settings used everywhere in this project are the validation winners for all three models.

## Stability over 5 random splits (`jobs/ml_robustness.py`)

The whole experiment (split, balancing, features, training) repeated with seeds 1–5. Mean ± standard deviation;
each test set is ~261,000 requests with the real 2.9% attack rate.

| Model | Precision | Recall | **F1** | ROC-AUC | PR-AUC |
|---|---|---|---|---|---|
| Linear SVM | 0.9518 ± 0.0017 | 0.9888 ± 0.0013 | **0.9699 ± 0.0011** | 0.9989 ± 0.0004 | 0.9940 ± 0.0008 |
| Logistic Regression | 0.9478 ± 0.0011 | 0.9855 ± 0.0013 | **0.9663 ± 0.0009** | 0.9985 ± 0.0005 | 0.9929 ± 0.0012 |
| Factorization Machine (ours) | 0.9412 ± 0.0086 | 0.9819 ± 0.0036 | **0.9611 ± 0.0062** | 0.9971 ± 0.0009 | 0.9899 ± 0.0027 |

**McNemar's test** (same test requests, do the two models make different mistakes?):

| Seed | FM vs Linear SVM: only FM right / only SVM right, p | FM vs Logistic Regression: only FM right / only LR right, p |
|---|---|---|
| 1 | 245 / 532, p = 1.1e-24 | 288 / 519, p = 5.7e-16 |
| 2 | 251 / 405, p = 2.3e-9 | 301 / 394, p = 4.8e-4 |
| 3 | 229 / 266, p = 0.11 | 286 / 258, p = 0.25 |
| 4 | 244 / 302, p = 0.015 | 280 / 288, p = 0.77 |
| 5 | 239 / 404, p = 1.0e-10 | 277 / 388, p = 2.0e-5 |

**What this shows.** When training and test data come from the same mix, the linear SVM is the most
accurate and the most stable model; it beats the FM significantly (p < 0.05) on 4 of 5 splits, and
Logistic Regression beats it on 3 of 5. The FM also varies more between runs (F1 std 0.006 vs 0.001),
because its factor weights start from random values. Seed 42, used in the main table, was one of the FM's
better splits. All three models stay above 0.99 PR-AUC. The FM's advantage is elsewhere: it generalises
to a dataset it was not trained on (next section).

## Across datasets (`jobs/ml_crossdataset.py`, 5 seeds)

Trained on one labelled dataset, tested on the other. Each row is the mean of 5 seeds (the seed changes the
balanced training sample and the FM's random start).

| Experiment | Model | Precision | Recall | **F1 (mean ± std)** | False alarms (mean) |
|---|---|---|---|---|---|
| A: train Fwaf → test HttpParams | **Factorization Machine** | 0.9765 | 0.9594 | **0.9679 ± 0.0078** | 271 |
| A: train Fwaf → test HttpParams | Logistic Regression | 0.8622 | 0.9355 | 0.8974 ± 0.0112 | 1,757 |
| A: train Fwaf → test HttpParams | Linear SVM | 0.7544 | 0.9680 | 0.8477 ± 0.0143 | 3,719 |
| B: train HttpParams → test Fwaf | Logistic Regression | 0.3608 | 0.4784 | 0.4114 ± 0.0000 | 22,802 |
| B: train HttpParams → test Fwaf | Linear SVM | 0.2216 | 0.5153 | 0.3099 ± 0.0009 | 48,724 |
| B: train HttpParams → test Fwaf | Factorization Machine | 0.0959 | 0.7825 | 0.1693 ± 0.0451 | 216,126 |

Trained on the large Fwaf set (135,000 requests), the FM transfers far better than the linear models
on all 5 seeds: F1 0.968 against 0.897 and 0.848, with 6–14× fewer false alarms. The FM's pairwise factor
terms model which character n-grams appear *together*, which carries over between datasets better than the
linear models' single weights per n-gram. Trained only on the small HttpParams set (~30,000 parameter
values), every model fails on Fwaf's full URLs, the FM worst: with few examples its pairwise terms overfit.

**Conclusion for the project.** Inside one dataset the linear SVM is the most accurate model. The FM is
kept as the deployed model because a detector meets traffic it was not trained on, and in that setting
(given enough training data) it is the clear winner. Both results are reported.

## On the NASA logs (case study, no labels)

The FM model scored all 31,026 distinct NASA URLs and flagged **120** (0.39%, 1,754 requests).
Before the bias fix it flagged 1,083. Checked by hand, most of the 120 are **not attacks**:
searches containing words like `select` or `transcript` ("trans**script**"), pages under
`/msfc/description/` ("de**script**ion"), and quotes in search terms. The rest are malformed requests
(binary garbage, `/.../liftoff.html`, pasted HTML such as `/elv/new01.gif><li><a href=`).
No confirmed attack was found. This is a **domain-shift limitation**: the training data is from modern
attack datasets, the NASA logs are 1995 traffic without labels.

**Audit of all 120 flagged URLs** (`scripts/audit_nasa_flags.py`, rules written down from reading every URL):

| What the URL really is | URLs | Requests | Why the model flagged it |
|---|---|---|---|
| Site search (`/htbin/wais.pl?…`) | 44 | 55 | words such as `select`, `transcript`, apostrophes |
| Static page or image under `/msfc/description/` | 35 | 933 | "de**script**ion" shares character n-grams with `<script` |
| Malformed or mistyped request | 21 | 38 | pasted HTML, binary noise, back-slashes, another site's address in the path |
| Other ordinary page or file | 11 | 708 | unusual characters or length |
| CGI with parameters (Harvest broker, image maps) | 9 | 20 | long `key=value&…` strings |

**0 of 120** contain a real attack signature (SQL `union select`, `<script`, `../..`, `/etc/passwd`,
shell commands). The server answered 1,547 of the flagged requests with 200, 67 with 304 and 126 with 404.
On real 1995 traffic the false-alarm rate is 1,754 / 3,461,612 requests = **0.05%**.
