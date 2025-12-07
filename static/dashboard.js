// static/dashboard.js

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

function formatMoney(value) {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  return `${sign}$${abs.toFixed(2)}`;
}

async function loadSummary() {
  const data = await fetchJSON("/api/summary");

  document.getElementById("current-balance").textContent =
    formatMoney(data.current_balance);
  document.getElementById("net-this-month").textContent =
    formatMoney(data.net_this_month);
  document.getElementById("income-this-month").textContent =
    formatMoney(data.total_income_this_month);
  document.getElementById("spent-this-month").textContent =
    formatMoney(data.total_spent_this_month);
  document.getElementById("period-label").textContent = data.today;

  // Category chart
  const catCtx = document.getElementById("categoryChart").getContext("2d");
  const catLabels = data.by_category.map((x) => x.category);
  const catValues = data.by_category.map((x) => x.amount);

  new Chart(catCtx, {
    type: "doughnut",
    data: {
      labels: catLabels,
      datasets: [
        {
          data: catValues.map((v) => -v), // spending is negative; make positive for chart
        },
      ],
    },
    options: {
      plugins: {
        legend: { position: "bottom" },
      },
    },
  });

  // Trend chart
  const trendCtx = document.getElementById("trendChart").getContext("2d");
  const labels = data.trend.map((x) => x.label);
  const income = data.trend.map((x) => x.income);
  const spending = data.trend.map((x) => -x.spending); // spending is negative
  const net = data.trend.map((x) => x.net);

  new Chart(trendCtx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Income", data: income },
        { label: "Spending", data: spending },
        { label: "Net", data: net },
      ],
    },
    options: {
      responsive: true,
      scales: {
        y: {
          ticks: {
            callback: (value) => `$${value}`,
          },
        },
      },
    },
  });
}

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
      if (e.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
  });

  return td;
}

async function loadTransactions() {
  const data = await fetchJSON("/api/transactions?limit=300");
  const tbody = document.querySelector("#tx-table tbody");
  tbody.innerHTML = "";

  for (const tx of data.transactions) {
    const tr = document.createElement("tr");
    if (tx.notes && tx.notes.toLowerCase().includes("pending")) {
      tr.classList.add("pending-row");
    }

    // Date
    const tdDate = document.createElement("td");
    tdDate.textContent = tx.date;
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
}

async function saveTransactionField(id, payload) {
  try {
    await fetchJSON(`/api/transactions/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("Failed to save", e);
    // You could add a toast/snackbar here later
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadSummary().catch(console.error);
  loadTransactions().catch(console.error);
});
