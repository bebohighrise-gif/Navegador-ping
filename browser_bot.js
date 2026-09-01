const fs = require('fs');
const puppeteer = require('puppeteer-core');

async function navegarYCapturar(url, outputPath = '/workspace/screenshot.png') {
    const browser = await puppeteer.launch({
        executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium-browser',
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu'
        ]
    });

    try {
        const page = await browser.newPage();
        await page.setViewport({ width: 1280, height: 800 });

        console.log(`[PUPPETEER] Navegando a: ${url}...`);
        await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });

        await page.screenshot({ path: outputPath, fullPage: false });
        console.log(`[PUPPETEER] ✅ Captura guardada en: ${outputPath}`);

        const pageTitle = await page.title();
        const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 1000));

        console.log(`[PUPPETEER] 📌 Título: ${pageTitle}`);
        console.log(`[PUPPETEER] 📝 Extracto:\n${bodyText}`);

    } catch (error) {
        console.error(`[PUPPETEER] ❌ Error durante la navegación: ${error.message}`);
    } finally {
        await browser.close();
    }
}

const targetUrl = process.argv[2] || 'https://google.com';
const outputImg = process.argv[3] || '/workspace/screenshot.png';

navegarYCapturar(targetUrl, outputImg);
