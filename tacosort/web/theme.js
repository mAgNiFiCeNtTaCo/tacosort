// Shows the look this phone used last before anything is drawn (app.js then asks TacoSort for the saved one).
"use strict";
try {
  const saved = localStorage.getItem("tacosort-theme");
  if (saved) document.documentElement.dataset.theme = saved;
} catch (e) { /* private browsing: the page starts in the phone's light or dark */ }
