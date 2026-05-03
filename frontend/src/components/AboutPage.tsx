/**
 * AboutPage — single-page story for the /about route.
 *
 * Persona-20 (skeptic visitor) lands here from a search result and needs
 * a 30-second read that answers "what is this, who made it, where do
 * the numbers come from, is it trustworthy". Persona-19 (non-English
 * speaker) sees it in EN/FR/IT — every string flows through the i18n
 * dictionary; nothing is hardcoded. Persona-16 (educator) gets a quick
 * primer with the data source + method explicitly named.
 *
 * Wired up in App.tsx with a tiny client-side router (pathname check)
 * so we don't pull in react-router for a single static page.
 */
import React from 'react';
import { useT, SUPPORTED_LOCALES, type Locale } from '../i18n';

const REPO_URL = 'https://github.com/edito-mheat/mheat';
const STAC_URL = '/api/stac/collections';
const OGC_URL = '/api/ogcapi';
const DATALAB_URL = '/tutorials/edito_datalab.ipynb';
const API_DOCS_URL = '/api/docs';

interface Props {
  /** Optional handler to navigate back to the dashboard without a full
      page reload. When omitted, the back link uses a normal href and the
      browser performs the navigation itself. */
  onBack?: () => void;
}

export function AboutPage({ onBack }: Props) {
  const { t, locale, setLocale } = useT();

  const handleBack = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (!onBack) return;
    e.preventDefault();
    onBack();
  };

  return (
    <div className="about-page">
      <header className="about-header">
        <div className="about-header-inner">
          <a
            className="about-back"
            href="/"
            onClick={handleBack}
            aria-label={t('about.back')}
          >
            {t('about.back')}
          </a>
          <span className="brand-mark" aria-label="MHEAT">MHEAT</span>
          <label className="lang-switch" aria-label={t('header.language')}>
            <select
              value={locale}
              onChange={(e) => setLocale(e.target.value as Locale)}
              className="select lang-select"
              aria-label={t('header.language')}
            >
              {SUPPORTED_LOCALES.map((l) => (
                <option key={l} value={l}>{l.toUpperCase()}</option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <main className="about-main" id="main">
        <h1 className="about-title">{t('about.title')}</h1>
        <p className="about-tagline">{t('about.tagline')}</p>

        <section className="about-section" aria-labelledby="about-what">
          <h2 id="about-what">{t('about.whatTitle')}</h2>
          <p>{t('about.whatBody')}</p>
        </section>

        <section className="about-section" aria-labelledby="about-who">
          <h2 id="about-who">{t('about.whoTitle')}</h2>
          <p>{t('about.whoBody')}</p>
        </section>

        <section className="about-section" aria-labelledby="about-data">
          <h2 id="about-data">{t('about.dataTitle')}</h2>
          <p>{t('about.dataBody')}</p>
        </section>

        <section className="about-section about-grant" aria-labelledby="about-grant">
          <h2 id="about-grant">{t('about.grantTitle')}</h2>
          <p>{t('about.grantBody')}</p>
        </section>

        <section className="about-section" aria-labelledby="about-tech">
          <h2 id="about-tech">{t('about.techTitle')}</h2>
          <p>{t('about.techBody')}</p>
        </section>

        <section className="about-section" aria-labelledby="about-links">
          <h2 id="about-links">{t('about.linksTitle')}</h2>
          <ul className="about-links">
            <li>
              <a href={REPO_URL} target="_blank" rel="noreferrer">
                {t('about.linkGithub')}
              </a>
            </li>
            <li>
              <a href={STAC_URL} target="_blank" rel="noreferrer">
                {t('about.linkStac')}
              </a>
            </li>
            <li>
              <a href={OGC_URL} target="_blank" rel="noreferrer">
                {t('about.linkOgc')}
              </a>
            </li>
            <li>
              <a href={DATALAB_URL} target="_blank" rel="noreferrer">
                {t('about.linkDatalab')}
              </a>
            </li>
            <li>
              <a href={API_DOCS_URL} target="_blank" rel="noreferrer">
                {t('about.linkApiDocs')}
              </a>
            </li>
          </ul>
        </section>

        <section className="about-section" aria-labelledby="about-license">
          <h2 id="about-license">{t('about.licenseTitle')}</h2>
          <p>{t('about.licenseBody')}</p>
        </section>
      </main>

      <footer className="footer">
        <span className="footer-group">
          <span className="footer-label">{t('footer.data')}</span>
          Copernicus Marine, EMODnet, EEA Natura 2000
        </span>
        <span className="footer-group">
          <span className="footer-label">{t('footer.method')}</span>
          Hobday et al. 2016
        </span>
      </footer>
    </div>
  );
}
