import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

// Import translations
import enCommon from './locales/en/common';
import zhCommon from './locales/zh/common';
import enSkill from './locales/en/skill';
import zhSkill from './locales/zh/skill';
import enTown from './locales/en/town';
import zhTown from './locales/zh/town';

// Combine translations
const resources = {
    en: {
        translation: {
            ...enCommon,
            skill: enSkill,
            town: enTown,
        }
    },
    zh: {
        translation: {
            ...zhCommon,
            skill: zhSkill,
            town: zhTown,
        }
    }
};

i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
        debug: false,
        fallbackLng: 'zh',
        interpolation: {
            escapeValue: false,
        },
        resources
    });

export default i18n;
