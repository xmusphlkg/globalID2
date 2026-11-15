export interface AcknowledgementRole {
  en: string;
  zh: string;
  tone?: 'support';
}

export interface AcknowledgementEntry {
  name: string;
  nameZh?: string;
  affiliationEn: string;
  affiliationZh: string;
  roles: AcknowledgementRole[];
  contributionEn: string;
  contributionZh: string;
}

export const acknowledgementIntro = {
  kickerEn: 'Acknowledgements',
  kickerZh: '致谢',
  titleEn: 'Acknowledgements and Support',
  titleZh: '致谢与支持',
  leadEn:
    'GIDS has benefited from the intellectual guidance, collegial feedback, and practical support of colleagues and mentors. The author gratefully acknowledges the following individuals for their contributions to the development of this project.',
  leadZh:
    'GIDS 的建设与完善，得益于多位同行与师长在学术思考、内容反馈和实际支持方面的帮助。作者谨向以下人员在本项目发展过程中所作出的贡献致以诚挚谢意。',
  noteEn:
    'This acknowledgement record will be maintained and expanded as the project continues to develop.',
  noteZh: '本致谢名单将随项目推进持续补充与更新。',
} as const;

// Add future acknowledgements here. Each entry will be rendered automatically on the About page.
export const acknowledgementEntries: AcknowledgementEntry[] = [
  {
    name: 'Tianmu Chen',
    nameZh: '陈田木',
    affiliationEn: 'School of Public Health, Xiamen University',
    affiliationZh: '厦门大学公共卫生学院',
    roles: [
      { en: 'Academic Advice', zh: '学术建议' },
      { en: 'Financial Support', zh: '资金支持', tone: 'support' },
    ],
    contributionEn:
      'Provided both thoughtful advice and financial support, enabling the project to continue its development and maintenance.',
    contributionZh:
      '不仅提供了富有价值的建议，也给予了资金支持，使项目得以持续开发与维护。',
  },
  {
    name: 'Benjamin Rader',
    affiliationEn: "Harvard Medical School; Boston Children's Hospital",
    affiliationZh: '哈佛医学院；波士顿儿童医院',
    roles: [
      { en: 'Academic Advice', zh: '学术建议' },
    ],
    contributionEn:
      "Provided valuable comments on the site's structure, content organisation, and overall presentation.",
    contributionZh:
      '就网站结构、内容组织与整体呈现提供了宝贵意见与专业建议。',
  },
  {
    name: 'Hong Liu',
    nameZh: '刘弘',
    affiliationEn: 'School of Public Health, Xiamen University',
    affiliationZh: '厦门大学公共卫生学院',
    roles: [
      { en: 'AI API Sponsorship', zh: 'AI API 赞助', tone: 'support' },
      { en: 'Information Sharing', zh: '信息共享' },
    ],
    contributionEn:
      'Provided AI API sponsorship and shared valuable information to support the development and maintenance of the project.',
    contributionZh:
      '为项目提供 AI API 赞助，并分享有价值的信息，支持项目的开发与维护。',
  },
];
