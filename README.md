# Ice House Toys: Christmas 2010 capacity simulator

**Open the app:** https://ice-house-simulator.streamlit.app/

Streamlit app for the Ice House Toys case (Operations Management, International MBA, IAE Paris-Sorbonne).
It simulates capacity, bottleneck, takt vs cycle time, backlog, stock replenishment and cost per parcel
(Appendix 1, 2009 vs new scenario) for the 2010 Christmas season.

If the app has not been used for a while, Streamlit puts it to sleep: it takes about 30 seconds to wake up.

## Run locally

```
python -m pip install -r requirements.txt
python -m streamlit run simulator.py
```

## Deploy

Streamlit Community Cloud: New app → this repository → main file `simulator.py`.
The published app reads the `main` branch and redeploys on every push.
