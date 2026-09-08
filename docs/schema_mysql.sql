-- i-STOCK MySQL schema (generated)
CREATE DATABASE IF NOT EXISTS istock CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE istock;

CREATE TABLE categories (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(80) NOT NULL, 
	description VARCHAR(255), 
	PRIMARY KEY (id), 
	UNIQUE (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE clients (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(120) NOT NULL, 
	contact_person VARCHAR(120), 
	phone VARCHAR(30), 
	address VARCHAR(255), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE locations (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(30) NOT NULL, 
	zone VARCHAR(50), 
	description VARCHAR(255), 
	PRIMARY KEY (id), 
	UNIQUE (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE settings (
	`key` VARCHAR(50) NOT NULL, 
	value VARCHAR(255) NOT NULL, 
	PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE suppliers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(120) NOT NULL, 
	contact_person VARCHAR(120), 
	phone VARCHAR(30), 
	email VARCHAR(120), 
	address VARCHAR(255), 
	lead_time_days FLOAT NOT NULL, 
	lead_time_std FLOAT NOT NULL, 
	is_active BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	username VARCHAR(50) NOT NULL, 
	full_name VARCHAR(120) NOT NULL, 
	email VARCHAR(120), 
	password_hash VARCHAR(255) NOT NULL, 
	`role` VARCHAR(20) NOT NULL, 
	is_active_user BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	last_login_at DATETIME, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE items (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	sku VARCHAR(40) NOT NULL, 
	barcode VARCHAR(64), 
	name VARCHAR(150) NOT NULL, 
	description VARCHAR(255), 
	unit VARCHAR(20) NOT NULL, 
	category_id INTEGER, 
	supplier_id INTEGER, 
	location_id INTEGER, 
	unit_cost NUMERIC(12, 2) NOT NULL, 
	unit_price NUMERIC(12, 2) NOT NULL, 
	quantity_on_hand INTEGER NOT NULL, 
	min_stock INTEGER NOT NULL, 
	service_level FLOAT, 
	lead_time_override FLOAT, 
	is_active BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	avg_daily_sales FLOAT NOT NULL, 
	demand_std FLOAT NOT NULL, 
	safety_stock INTEGER NOT NULL, 
	reorder_point INTEGER NOT NULL, 
	forecast_30d FLOAT NOT NULL, 
	forecast_model VARCHAR(20), 
	recommended_qty INTEGER NOT NULL, 
	metrics_updated_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(category_id) REFERENCES categories (id), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE purchase_orders (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	po_number VARCHAR(30) NOT NULL, 
	supplier_id INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	auto_generated BOOL NOT NULL, 
	notes VARCHAR(255), 
	created_by_id INTEGER, 
	approved_by_id INTEGER, 
	created_at DATETIME NOT NULL, 
	sent_at DATETIME, 
	expected_date DATE, 
	received_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (po_number), 
	FOREIGN KEY(supplier_id) REFERENCES suppliers (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id), 
	FOREIGN KEY(approved_by_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE sales_orders (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	order_number VARCHAR(30) NOT NULL, 
	client_id INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	notes VARCHAR(255), 
	created_by_id INTEGER, 
	created_at DATETIME NOT NULL, 
	dispatched_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (order_number), 
	FOREIGN KEY(client_id) REFERENCES clients (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE batches (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	item_id INTEGER NOT NULL, 
	batch_no VARCHAR(50) NOT NULL, 
	expiry_date DATE, 
	quantity INTEGER NOT NULL, 
	received_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_batch_item_no UNIQUE (item_id, batch_no), 
	FOREIGN KEY(item_id) REFERENCES items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE purchase_order_lines (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	po_id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	quantity_ordered INTEGER NOT NULL, 
	quantity_received INTEGER NOT NULL, 
	unit_cost NUMERIC(12, 2) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(po_id) REFERENCES purchase_orders (id), 
	FOREIGN KEY(item_id) REFERENCES items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE sales_order_lines (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	order_id INTEGER NOT NULL, 
	item_id INTEGER NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit_price NUMERIC(12, 2) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(order_id) REFERENCES sales_orders (id), 
	FOREIGN KEY(item_id) REFERENCES items (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE stock_transactions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	item_id INTEGER NOT NULL, 
	batch_id INTEGER, 
	txn_type VARCHAR(10) NOT NULL, 
	quantity INTEGER NOT NULL, 
	balance_after INTEGER NOT NULL, 
	unit_value NUMERIC(12, 2), 
	reference VARCHAR(60), 
	note VARCHAR(255), 
	source VARCHAR(20), 
	client_id INTEGER, 
	purchase_order_id INTEGER, 
	sales_order_id INTEGER, 
	user_id INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(item_id) REFERENCES items (id), 
	FOREIGN KEY(batch_id) REFERENCES batches (id), 
	FOREIGN KEY(client_id) REFERENCES clients (id), 
	FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders (id), 
	FOREIGN KEY(sales_order_id) REFERENCES sales_orders (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

