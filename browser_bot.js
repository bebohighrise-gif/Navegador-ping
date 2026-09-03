#!/usr/bin/env node
/**
 * Bot de navegación con Puppeteer (Chromium headless).
 * Uso: node browser_bot.js <url> [ruta_screenshot]
 */
"use strict";

const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

async function navegarYCapturar(url, outputPath = "/workspace/screenshot.png") {
  const executablePath =
    process.env.PUPPETEER_EXECUTABLE_PATH ||
    process.env.CHROME_PATH ||
    "/usr/bin/chromium";

  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath,
      headless: true,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--font-render-hinting=none",
      ],
      defaultViewport: { width: 1280, height: 800 },
    });

    const page = await browser.newPage();
    page.setDefaultNavigationTimeout(45000);
    page.setDefaultTimeout(30000);

    console.log(`[PUPPETEER] Navegando a: ${url}`);
    await page.goto(url, {
      waitUntil: "networkidle2",
      timeout: 45000,
    });

    // Asegurar directorio de salida
    const dir = path.dirname(outputPath);
    if (dir && dir !== ".") {
      fs.mkdirSync(dir, { recursive: true });
    }

    await page.screenshot({ path: outputPath, fullPage: false, type: "png" });
    console.log(`[PUPPETEER] ✅ Captura guardada en: ${outputPath}`);

    const pageTitle = await page.title();
    const bodyText = await page.evaluate(() => {
      const text = document.body ? document.body.innerText : "";
      return text.substring(0, 1500);
    });

    console.log(`[PUPPETEER] 📌 Título: ${pageTitle}`);
    console.log(`[PUPPETEER] 📝 Extracto:\n${bodyText}`);

    return { title: pageTitle, text: bodyText, screenshot: outputPath };
  } catch (error) {
    console.error(`[PUPPETEER] ❌ Error: ${error.message}`);
    process.exitCode = 1;
    throw error;
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

const targetUrl = process.argv[2] || "https://example.com";
const outputImg = process.argv[3] || "/workspace/screenshot.png";

if (!/^https?:\/\//i.test(targetUrl)) {
  console.error("La URL debe empezar por http:// o https://");
  process.exit(1);
}

navegarYCapturar(targetUrl, outputImg).catch(() => process.exit(1));
