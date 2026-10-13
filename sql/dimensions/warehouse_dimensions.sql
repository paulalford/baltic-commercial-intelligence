-- Milestone 7 conformed dimensions and effective-dated bridges.
-- All source objects are validated, connection-local Silver Arrow registrations.

CREATE TABLE dim_date AS
SELECT
    date_key,
    full_date,
    calendar_year,
    calendar_quarter,
    calendar_month,
    month_start_date,
    iso_week,
    iso_week_year,
    iso_weekday,
    day_of_month,
    day_of_year,
    is_weekend,
    is_within_reporting_window
FROM silver_date_dimension
ORDER BY full_date;

CREATE TABLE dim_geography AS
SELECT
    city.city_id,
    city.city_name,
    region.region_id,
    region.region_name,
    region.region_scheme,
    country.country_code,
    country.country_name,
    country.reporting_currency
FROM silver_cities AS city
JOIN silver_regions AS region ON region.region_id = city.region_id
JOIN silver_countries AS country ON country.country_code = city.country_code
ORDER BY city.city_id;

CREATE TABLE dim_channel AS
SELECT channel_code, channel_name
FROM silver_sales_channels
ORDER BY channel_code;

CREATE TABLE dim_customer AS
SELECT
    row_number() OVER (
        ORDER BY history.customer_id, history.valid_from, history.customer_version_id
    )::BIGINT AS customer_sk,
    history.customer_version_id,
    customer.customer_id,
    customer.customer_name,
    customer.acquisition_date,
    customer.account_status,
    customer.industry_code,
    industry.industry_name,
    customer.credit_tier,
    customer.preferred_channel_code,
    history.valid_from,
    history.valid_to,
    history.segment_code,
    segment.segment_name,
    history.company_size_band,
    history.home_city_id,
    history.home_region_id,
    history.home_country_code,
    history.is_current
FROM silver_customer_attribute_history AS history
JOIN silver_customers AS customer USING (customer_id)
JOIN silver_industries AS industry USING (industry_code)
JOIN silver_customer_segments AS segment USING (segment_code)
ORDER BY history.customer_id, history.valid_from, history.customer_version_id;

CREATE TABLE dim_product AS
SELECT
    product.product_id,
    product.sku,
    product.product_name,
    product.subcategory_id,
    subcategory.subcategory_name,
    subcategory.category_id,
    category.category_name,
    product.launch_date,
    product.discontinued_date,
    product.lifecycle_status,
    product.reference_list_unit_price_eur,
    product.reference_standard_unit_cost_eur
FROM silver_products AS product
JOIN silver_product_subcategories AS subcategory USING (subcategory_id)
JOIN silver_product_categories AS category USING (category_id)
ORDER BY product.product_id;

CREATE TABLE dim_salesperson AS
SELECT
    salesperson_id,
    salesperson_name,
    primary_country_code,
    primary_region_id,
    hire_date,
    termination_date,
    manager_salesperson_id
FROM silver_salespeople
ORDER BY salesperson_id;

CREATE TABLE dim_supplier AS
SELECT supplier_id, supplier_name, supplier_country_code
FROM silver_suppliers
ORDER BY supplier_id;

CREATE TABLE bridge_customer_owner AS
SELECT assignment_id, customer_id, salesperson_id, valid_from, valid_to
FROM silver_customer_account_assignments
ORDER BY customer_id, valid_from, assignment_id;

CREATE TABLE bridge_product_supplier AS
SELECT
    product_supplier_relationship_id,
    product_id,
    supplier_id,
    relationship_type,
    valid_from,
    valid_to
FROM silver_product_supplier_relationships
ORDER BY product_id, valid_from, product_supplier_relationship_id;
