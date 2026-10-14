-- Posted EUR actuals by complete month and posting-time customer-home geography.
-- A missing country/month has zero postings; finance validation independently checks it.
CREATE TABLE sales_monthly AS
WITH calendar AS (
 SELECT month::DATE AS reporting_month
 FROM build_parameters p,
      generate_series(p.period_start_month,p.as_of_month,INTERVAL '1 month') t(month)
), country_source AS (
 SELECT g.reporting_month,g.home_country_code,
        sum(g.net_revenue_eur)::DECIMAL(18,2) AS net_revenue_eur,
        sum(g.net_gross_profit_eur)::DECIMAL(18,2) AS net_gross_profit_eur,
        sum(g.posting_count)::BIGINT AS posting_count
 FROM gold.mart_commercial_month g CROSS JOIN build_parameters p
 WHERE g.reporting_month BETWEEN p.period_start_month AND p.as_of_month
 GROUP BY 1,2
), company_source AS (
 SELECT g.reporting_month,g.net_revenue_eur,g.net_gross_profit_eur,g.posting_count
 FROM gold.mart_company_month g CROSS JOIN build_parameters p
 WHERE g.reporting_month BETWEEN p.period_start_month AND p.as_of_month
)
SELECT c.reporting_month,'country'::VARCHAR scope_type,market.code::VARCHAR scope_code,
       coalesce(s.net_revenue_eur,0)::DECIMAL(18,2) net_revenue_eur,
       coalesce(s.net_gross_profit_eur,0)::DECIMAL(18,2) net_gross_profit_eur,
       coalesce(s.posting_count,0)::BIGINT posting_count,
       (s.reporting_month IS NOT NULL)::BOOLEAN source_month_present,
       CASE WHEN coalesce(s.net_revenue_eur,0)>0 THEN
         (s.net_gross_profit_eur::DOUBLE/s.net_revenue_eur::DOUBLE) END gross_margin_rate,
       'customer_home_at_posting'::VARCHAR geography_role,
       'posting_date'::VARCHAR date_basis,
       'EUR'::VARCHAR currency_code,
       p.as_of_date
FROM calendar c CROSS JOIN (VALUES ('LT'),('LV'),('EE')) market(code)
CROSS JOIN build_parameters p
LEFT JOIN country_source s ON s.reporting_month=c.reporting_month
 AND s.home_country_code=market.code
UNION ALL
SELECT c.reporting_month,'company'::VARCHAR scope_type,'COMPANY'::VARCHAR scope_code,
       coalesce(s.net_revenue_eur,0)::DECIMAL(18,2),
       coalesce(s.net_gross_profit_eur,0)::DECIMAL(18,2),
       coalesce(s.posting_count,0)::BIGINT,
       (s.reporting_month IS NOT NULL)::BOOLEAN,
       CASE WHEN coalesce(s.net_revenue_eur,0)>0 THEN
         (s.net_gross_profit_eur::DOUBLE/s.net_revenue_eur::DOUBLE) END,
       'customer_home_at_posting'::VARCHAR,'posting_date'::VARCHAR,'EUR'::VARCHAR,
       p.as_of_date
FROM calendar c CROSS JOIN build_parameters p
LEFT JOIN company_source s ON s.reporting_month=c.reporting_month
ORDER BY reporting_month,scope_code;
