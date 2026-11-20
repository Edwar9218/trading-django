from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_perfilusuario_pref_secuencia"),
    ]

    operations = [
        migrations.AddField(
            model_name="perfilusuario",
            name="pref_tzt",
            field=models.BooleanField(default=False),
        ),
    ]
