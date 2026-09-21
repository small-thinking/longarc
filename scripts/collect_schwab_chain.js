/**
 * Read-only bridge for the documented Codex browser tool. Run inside cua_repl,
 * using its already-selected tab; this is NOT a standalone browser driver.
 * No cookies, network interception, order controls, login or account settings.
 * `observe` must read current UI state (e.g. cuaTab.getAXState({emit:false})).
 * Pass expiry dates and strike centers observed in the page, plus watched contracts.
 */
async function collectSchwabChain(tab, options, observe) {
  const symbol = options.symbol === undefined ? "QQQ" : options.symbol;
  if (typeof symbol !== "string" || symbol.trim() !== symbol ||
      !/^[A-Z][A-Z0-9.-]{0,14}$/.test(symbol)) {
    throw new Error("symbol must be an explicit uppercase ticker");
  }
  const start = new Date().toISOString();
  const requested = {
    expiries: options.expiries,
    strike_centers: options.strikeCenters,
    contracts: options.contracts || [],
  };
  if (!Array.isArray(requested.expiries) || !requested.expiries.length ||
      requested.expiries.length > 12 || !Array.isArray(requested.strike_centers) ||
      !requested.strike_centers.length || requested.strike_centers.length > 12) {
    throw new Error("Choose 1-12 observed expiries and 1-12 observed strike centers");
  }
  const result = {
    format: "schwab-browser-chain-v1", symbol, started_at: start,
    captured_at: start, requested, slices: [], errors: [],
    completeness: "visible_windows_only_not_full_listed_chain",
    evidence_ids: [],
  };
  const months = {Jan:1, Feb:2, Mar:3, Apr:4, May:5, Jun:6,
    Jul:7, Aug:8, Sep:9, Oct:10, Nov:11, Dec:12};
  const dateIn = (text) => {
    const m = text.match(/([A-Z][a-z]{2})\.\s+(\d+),\s+(\d{4})/);
    return m && months[m[1]] ?
      `${m[3]}-${String(months[m[1]]).padStart(2,"0")}-${m[2].padStart(2,"0")}` : null;
  };
  // A bounded retry re-observes the UI; never repeatedly clicks a toggle on error.
  const readWindow = async (expiry) => {
    for (let attempt = 1; attempt <= 3; attempt++) {
      const tables = await tab.playwright.getByRole("table").evaluateAll(elements =>
        elements.map(el => {
          const region = el.closest('[id^="chains-accord-control-"]');
          return {
            region: region ? region.id : null,
            label: region?.parentElement?.querySelector("h2")?.textContent ||
              el.closest("pf3-sdps-accordion-section")?.innerText.split("\n")[0] || "",
            headers: Array.from(el.querySelectorAll("tr"))[0] ?
              Array.from(el.querySelectorAll("tr"))[0].innerText : "",
            cells: Array.from(el.querySelectorAll("tr")).map(row =>
              Array.from(row.querySelectorAll("th,td")).map(cell => cell.innerText.trim().split("\n")[0])),
          };
        })
      );
      const found = tables.find(t => dateIn(t.label) === expiry && t.cells.length > 1);
      if (found) return found;
      await observe();
    }
    return null;
  };
  try {
    const page = new URL(await tab.url());
    const route = page.hash.match(/^#\/(?:etfs|stocks)\/options\/([^/?#]+)\/?(?:\?[^#]*)?$/);
    if (page.protocol !== "https:" || page.hostname !== "client.schwab.com" ||
        !/^\/retail\/research\/?$/.test(page.pathname) || !route || route[1] !== symbol) {
      throw new Error(`Open the ${symbol} research Options tab first`);
    }
    await tab.playwright.getByLabel("Strategy", {exact:true}).selectOption({label:"Calls"});
    await observe();
    await tab.playwright.getByLabel("Strikes", {exact:true}).selectOption({label:"All"});
    await observe();
    if (options.includeGreeks) {
      await tab.playwright.getByRole("button", {name:"Customize", exact:true}).click();
      await observe();
      await tab.playwright.getByRole("button", {name:"Edit columns", exact:true}).click();
      await observe();
      for (const name of ["Implied Volatility (IV)", "Gamma", "Vega", "Bid Size", "Ask Size"]) {
        await tab.playwright.getByRole("checkbox", {name, exact:true}).check();
      }
      await tab.playwright.getByRole("button", {name:"Next", exact:true}).click();
      await observe();
      // Arrange Columns exposes the Save control with accessible name Next.
      await tab.playwright.getByRole("button", {name:"Next", exact:true}).click();
      await observe();
    }
    // The visible expiry menu, not a guessed route, determines supported dates.
    const buttons = await tab.playwright.getByRole("button").allTextContents({});
    for (const expiry of requested.expiries) {
      const label = buttons.find(text => dateIn(text) === expiry && text.includes("Toggle"));
      if (!label) {
        result.errors.push({expiry_date:expiry, code:"expiry_not_in_visible_menu"});
        continue;
      }
      const button = tab.playwright.getByRole("button", {
        name: new RegExp(label.trim().split("Toggle")[0].trim().replace(/[.*+?^${}()|[\]\\]/g,"\\$&")
          .replace(/\s+/g,"\\s+") + ".*Toggle"),
      });
      if (await button.getAttribute("aria-expanded", {}) !== "true") {
        await button.click();
        await observe();
      }
      for (const center of requested.strike_centers) {
        try {
          await tab.playwright.getByRole("button", {name:"Filters", exact:true}).click();
          await observe();
          await tab.playwright.getByRole("combobox", {name:"Strike to center on", exact:true})
            .selectOption({label:String(center)});
          await tab.playwright.getByRole("button", {name:"Apply", exact:true}).click();
          await observe();
          const table = await readWindow(expiry);
          if (!table) {
            result.errors.push({expiry_date:expiry, center, code:"empty_table_after_3_reads"});
            continue;
          }
          result.slices.push({
            expiry_date:expiry, center, captured_at:new Date().toISOString(),
            quote_at:null, greeks_at:null, underlying_at:options.underlyingAt || null,
            underlying_price:options.underlyingPrice ?? null,
            underlying_basis:options.underlyingBasis || "unknown",
            headers:table.cells[0], rows:table.cells.slice(1), attempts_limit:3,
          });
        } catch (_) {
          result.errors.push({expiry_date:expiry, center, code:"window_read_failed"});
          // Do not interact with an unexpected modal/login dialog or lose good slices.
          result.captured_at = new Date().toISOString();
          return result;
        }
      }
    }
  } catch (_) {
    result.errors.push({code:"setup_failed_check_login_and_page_structure"});
  }
  result.captured_at = new Date().toISOString();
  return result;
}

if (typeof module !== "undefined") module.exports = {collectSchwabChain};
