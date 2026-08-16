const api = "/local-editor/api";
const form = document.querySelector("#card-form");
const status = document.querySelector("#status");
const search = document.querySelector("#player-search");
const results = document.querySelector("#player-results");
const attributes = [...document.querySelectorAll("[data-attribute]")];
const gameplayGroups = {
  Pace: ["acceleration", "sprintspeed"],
  Shooting: ["positioning", "finishing", "shotpower", "longshots", "volleys", "penalties"],
  Passing: ["vision", "crossing", "freekickaccuracy", "shortpassing", "longpassing", "curve"],
  Dribbling: ["agility", "balance", "reactions", "ballcontrol", "dribbling"],
  Defending: ["interceptions", "headingaccuracy", "marking", "standingtackle", "slidingtackle"],
  Physical: ["jumping", "stamina", "strength", "aggression"],
  Goalkeeping: ["gkdiving", "gkhandling", "gkkicking", "gkreflexes", "gkpositioning"],
};
const gameplayFields = Object.values(gameplayGroups).flat();
let selectedBase = null;
let searchTimer = null;

function labelFor(field) {
  return field.replace(/^gk/, "GK ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (letter) => letter.toUpperCase());
}

function createGameplayInputs() {
  const container = document.querySelector("#gameplay-fields");
  for (const [group, fields] of Object.entries(gameplayGroups)) {
    const section = document.createElement("section");
    section.className = "gameplay-group";
    const heading = document.createElement("h3");
    heading.textContent = group;
    const grid = document.createElement("div");
    grid.className = "stats-grid";
    for (const field of fields) {
      const label = document.createElement("label");
      label.textContent = labelFor(field);
      const input = document.createElement("input");
      input.id = `stat-${field}`;
      input.type = "number";
      input.min = "1";
      input.max = "99";
      input.required = true;
      label.append(input);
      grid.append(label);
    }
    section.append(heading, grid);
    container.append(section);
  }
}

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

function setCard(card) {
  document.querySelector("#card-id").value = card.cardId || "";
  document.querySelector("#asset-id").value = card.assetId;
  document.querySelector("#player-name").textContent = card.name || "Selected player";
  document.querySelector("#rating").value = card.rating;
  document.querySelector("#rare-flag").value = String(card.rareFlag);
  document.querySelector("#team-id").value = card.teamId;
  document.querySelector("#league-id").value = card.leagueId;
  attributes.forEach((input, index) => { input.value = card.attributes[index]; });
  for (const field of gameplayFields) document.querySelector(`#stat-${field}`).value = card.gameplayAttributes[field];
  for (const field of ["weakfootabilitytypecode", "skillmoves", "preferredfoot", "attackingworkrate", "defensiveworkrate"]) {
    document.querySelector(`#${field}`).value = String(card.traits[field]);
  }
}

function formData() {
  return {
    assetId: Number(document.querySelector("#asset-id").value),
    rating: Number(document.querySelector("#rating").value),
    rareFlag: Number(document.querySelector("#rare-flag").value),
    teamId: Number(document.querySelector("#team-id").value),
    leagueId: Number(document.querySelector("#league-id").value),
    attributes: attributes.map((input) => Number(input.value)),
    gameplayAttributes: Object.fromEntries(gameplayFields.map((field) => [field, Number(document.querySelector(`#stat-${field}`).value)])),
    traits: Object.fromEntries(["weakfootabilitytypecode", "skillmoves", "preferredfoot", "attackingworkrate", "defensiveworkrate"].map((field) => [field, Number(document.querySelector(`#${field}`).value)])),
  };
}

async function request(path, options = {}) {
  const response = await fetch(`${api}${path}`, options);
  const document = await response.json();
  if (!response.ok) throw new Error(document.error || "Request failed");
  return document;
}

async function searchPlayers() {
  const query = search.value.trim();
  if (query.length < 2) { results.replaceChildren(); return; }
  const responseDocument = await request(`/players?q=${encodeURIComponent(query)}`);
  results.replaceChildren(...responseDocument.players.map((player) => {
    const option = document.createElement("option");
    option.value = String(player.assetId);
    option.textContent = `${player.name} | ${player.rating} ${player.position}`;
    option.dataset.player = JSON.stringify(player);
    return option;
  }));
}

function resetToBase() {
  if (!selectedBase) return;
  setCard(selectedBase);
  document.querySelector("#card-id").value = "";
}

async function loadCards() {
  const responseDocument = await request("/cards");
  const list = document.querySelector("#saved-cards");
  list.replaceChildren(...responseDocument.cards.map((card) => {
    const item = document.createElement("li");
    const text = document.createElement("div");
    const name = document.createElement("span");
    const meta = document.createElement("span");
    name.className = "saved-name";
    meta.className = "saved-meta";
    name.textContent = `${card.name} ${card.rating}`;
    meta.textContent = `v${card.version} | ${card.position} | ${card.cardType}`;
    text.append(name, meta);
    const actions = document.createElement("div");
    actions.className = "saved-actions";
    for (const [label, action] of [["Edit", "edit"], ["Grant", "grant"], ["Delete", "delete"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", async () => {
        if (action === "edit") { setCard(card); selectedBase = card; return; }
        if (action === "delete") { await request(`/cards/${card.cardId}`, { method: "DELETE" }); await loadCards(); return; }
        await request(`/cards/${card.cardId}/grant`, { method: "POST" }); setStatus("Card granted to My Club.");
      });
      actions.append(button);
    }
    item.append(text, actions);
    return item;
  }));
}

search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchPlayers().catch((error) => setStatus(error.message, true)), 180);
});
results.addEventListener("change", () => {
  const option = results.selectedOptions[0];
  if (!option) return;
  selectedBase = JSON.parse(option.dataset.player);
  setCard(selectedBase);
});
document.querySelector("#reset").addEventListener("click", resetToBase);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const cardId = document.querySelector("#card-id").value;
    const saved = await request(cardId ? `/cards/${cardId}` : "/cards", {
      method: cardId ? "PUT" : "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ card: formData(), grant: document.querySelector("#grant").checked }),
    });
    setCard(saved.card);
    setStatus(saved.granted ? "Card saved and granted." : "Card saved.");
    await loadCards();
  } catch (error) { setStatus(error.message, true); }
});
document.querySelector("#import").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    await request("/cards/import", { method: "POST", headers: { "content-type": "application/json" }, body: await file.text() });
    setStatus("Cards imported.");
    await loadCards();
  } catch (error) { setStatus(error.message, true); }
  event.target.value = "";
});
createGameplayInputs();
loadCards().catch((error) => setStatus(error.message, true));