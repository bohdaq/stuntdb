-- Original synthetic fixtures; framework-shaped, not upstream application dumps.
CREATE TABLE django_content_type (id INT PRIMARY KEY, app_label VARCHAR(100), model VARCHAR(100)) ENGINE=InnoDB;
CREATE TABLE django_user (id INT PRIMARY KEY, email VARCHAR(254)) ENGINE=InnoDB;
CREATE TABLE django_group (id INT PRIMARY KEY, name VARCHAR(150)) ENGINE=InnoDB;
CREATE TABLE django_membership (id INT PRIMARY KEY, user_id INT NOT NULL, group_id INT NOT NULL,
 FOREIGN KEY (user_id) REFERENCES django_user(id), FOREIGN KEY (group_id) REFERENCES django_group(id), UNIQUE(user_id, group_id)) ENGINE=InnoDB;
CREATE TABLE django_note (id INT PRIMARY KEY, user_id INT, parent_id INT, content_type_id INT, object_id INT,
 FOREIGN KEY (user_id) REFERENCES django_user(id), FOREIGN KEY (parent_id) REFERENCES django_note(id),
 FOREIGN KEY (content_type_id) REFERENCES django_content_type(id)) ENGINE=InnoDB;
INSERT INTO django_content_type VALUES (1,'zoo','group');
INSERT INTO django_user VALUES (1,'canary@example.invalid'), (2,'unselected@example.invalid');
INSERT INTO django_group VALUES (1,'selected'), (2,'unselected');
INSERT INTO django_membership VALUES (1,1,1),(2,2,2);
INSERT INTO django_note VALUES (1,1,NULL,1,2),(2,1,1,NULL,NULL);
CREATE TABLE wp_users (ID BIGINT UNSIGNED PRIMARY KEY, user_email VARCHAR(100), user_pass VARCHAR(255)) ENGINE=InnoDB;
CREATE TABLE wp_posts (ID BIGINT UNSIGNED PRIMARY KEY, post_author BIGINT UNSIGNED, post_parent BIGINT UNSIGNED, post_content LONGTEXT) ENGINE=InnoDB;
CREATE TABLE wp_postmeta (meta_id BIGINT UNSIGNED PRIMARY KEY, post_id BIGINT UNSIGNED, meta_key VARCHAR(255), meta_value LONGTEXT) ENGINE=InnoDB;
INSERT INTO wp_users VALUES (1,'canary@example.invalid','fake hash');
INSERT INTO wp_posts VALUES (1,1,0,'Synthetic content');
INSERT INTO wp_postmeta VALUES (1,1,'synthetic','value');
