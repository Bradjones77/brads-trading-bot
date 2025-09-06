# learner.py - online learner (kept similar) with joblib saving
import os, joblib
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler
import numpy as np

MODEL_DIR = os.getenv('MODEL_DIR','models')
os.makedirs(MODEL_DIR, exist_ok=True)

class OnlineLearner:
    def __init__(self, name='short', feature_names=None):
        self.name = name
        self.model_path = os.path.join(MODEL_DIR, f'model_{name}.joblib')
        self.scaler_path = os.path.join(MODEL_DIR, f'scaler_{name}.joblib')
        self.feature_names = feature_names or ['close','ret_1','ema9','ema21','rsi14','ema9_ema21_diff','macd_hist','atr','vol_mean_20']
        if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
            self.clf = joblib.load(self.model_path)
            self.scaler = joblib.load(self.scaler_path)
            self._initialized = True
        else:
            self.clf = SGDClassifier(loss='log', max_iter=1000, tol=1e-3)
            self.scaler = StandardScaler()
            self._initialized = False

    def predict_proba(self, features: dict):
        import numpy as np
        X = np.array([[features.get(n,0) for n in self.feature_names]], dtype=float)
        if not self._initialized:
            return 0.5
        Xs = self.scaler.transform(X)
        try:
            p = float(self.clf.predict_proba(Xs)[0][1])
            return p
        except Exception:
            return 0.5

    def partial_update(self, features_list, labels):
        import numpy as np
        X = np.array([[f.get(n,0) for n in self.feature_names] for f in features_list], dtype=float)
        y = np.array(labels, dtype=int)
        if not self._initialized:
            self.scaler.fit(X)
            self.clf.partial_fit(self.scaler.transform(X), y, classes=np.array([0,1]))
            self._initialized = True
        else:
            Xs = self.scaler.transform(X)
            self.clf.partial_fit(Xs, y)
        joblib.dump(self.clf, self.model_path)
        joblib.dump(self.scaler, self.scaler_path)
