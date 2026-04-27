// static/dashboard.js
// Note: dashboard metric cards, account grid, and debt thermometer are
// server-rendered by the /dashboard route. This file handles inline editing
// on the transactions page and provides shared fetch/format utilities.

// -------------------------
// Helpers
// -------------------------
async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  const contentType = res.headers.get("Content-Type") || "";

  if (!res.ok) {
    let body = "";
    try {
      body = await res.text();
    } catch (e) {
      body = "";
    }
    throw new Error(
      `HTTP ${res.status} ${res.statusText} for ${url} :: ${body.slice(0, 300)}`
    );
  }

  if (contentType.includes("application/json")) {
    return await res.json();
  }
  return {};
}

function formatMoney(value) {
  const num = Number(value || 0);
  const sign = num < 0 ? "-" : "";
  const abs = Math.abs(num);
  return `${sign}$${abs.toFixed(2)}`;
}

// -------------------------
// Editable cell helper
// -------------------------
function createEditableCell(tx, field, onSave) {
  const td = document.createElement("td");
  td.textContent = tx[field] || "";

  td.addEventListener("click", () => {
    if (td.querySelector("input")) return; // already editing

    const oldValue = td.textContent;
    td.textContent = "";

    const input = document.createElement("input");
    input.type = "text";
    input.value = oldValue;
    input.style.width = "100%";

    td.appendChild(input);
    input.focus();

    function finish(save) {
      const newVal = input.value.trim();
      td.removeChild(input);
      td.textContent = save ? newVal : oldValue;

      if (save && newVal !== oldValue) {
        onSave(newVal);
      }
    }

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
    });

    input.addEventListener("blur", () => finish(true));
  });

  return td;
}

// -------------------------
// Transactions table
// -------------------------
async function loadTransactions() {
  const tbody = document.querySelector("#tx-table tbody");
  if (!tbody) {
    console.warn("No #tx-table tbody found; skipping loadTransactions");
    return;
  }

  try {
    const data = await fetchJSON("/api/transactions?limit=300");
    const txs = data.transactions || [];
    console.log("Loaded transactions:", txs.length);

    tbody.innerHTML = "";

    for (const tx of txs) {
      const tr = document.createElement("tr");

      if (tx.notes && tx.notes.toLowerCase().includes("pending")) {
        tr.classList.add("pending-row");
      }

      // Date
      const tdDate = document.createElement("td");
      tdDate.textContent = tx.date || "";
      tr.appendChild(tdDate);

      // Amount
      const tdAmount = document.createElement("td");
      tdAmount.textContent = formatMoney(tx.amount);
      tr.appendChild(tdAmount);

      // Merchant (editable)
      tr.appendChild(
        createEditableCell(tx, "merchant", async (newVal) => {
          await saveTransactionField(tx.id, { merchant: newVal });
        })
      );

      // Category (editable)
      tr.appendChild(
        createEditableCell(tx, "category", async (newVal) => {
          await saveTransactionField(tx.id, { category: newVal });
        })
      );

      // Notes (editable)
      tr.appendChild(
        createEditableCell(tx, "notes", async (newVal) => {
          await saveTransactionField(tx.id, { notes: newVal });
        })
      );

      tbody.appendChild(tr);
    }
  } catch (err) {
    console.error("Failed to load transactions:", err);
  }
}

// -------------------------
// Save using the NEW endpoint
// -------------------------
async function saveTransactionField(id, payload) {
  const url = `/api/transactions/${id}`;  // <-- IMPORTANT
  console.log("Saving", id, payload);

  try {
    const res = await fetch(url, {
      method: "PUT",                   // backend supports PUT and POST
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const data = await res.json();
    console.log("Saved transaction:", data);
  } catch (e) {
    console.error("Failed to save", e);
    alert("Save failed — open DevTools (F12) and check Console + Network.");
  }
}

// -------------------------
// Init
// -------------------------
document.addEventListener("DOMContentLoaded", () => {
  loadTransactions();
});
